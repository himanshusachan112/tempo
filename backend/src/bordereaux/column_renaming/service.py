"""Column renaming for a run: suggestions (MCR-D1..D5), the reviewer's decisions, and the revised discrepancies.

Read-only with respect to the original flow: ``discrepancy_findings`` are never modified and no
mapping or standardized data changes. The only thing written is a ``ColumnRenameDecision`` (plus
an audit event); removing it restores the original reading.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bordereaux.audit.service import AuditService
from bordereaux.cleaning.parsing import try_parse_decimal
from bordereaux.column_renaming import detectors as d
from bordereaux.column_renaming.revised import AcceptedRename, revised_discrepancies
from bordereaux.core.exceptions import NotFoundError, StateError, ValidationError
from bordereaux.core.models import ActorType, AuditAction, ProcessingRunStatus
from bordereaux.financial_checks.relations_service import treaty_rates_of
from bordereaux.financial_checks.service import list_discrepancies
from bordereaux.models.column_rename import ColumnRenameDecision
from bordereaux.models.mapping import ApprovedMapping
from bordereaux.models.money_rules import MoneyRelation
from bordereaux.models.run import ProcessingRun
from bordereaux.models.treaty import TreatyFigures
from bordereaux.money_rules import MCRConfig, MoneyRole, TreatyRates
from bordereaux.schemas.loader import MONEY_ROLE_BY_FIELD, load_schema_definition
from bordereaux.standardization.service import load_standardized_dataset
from bordereaux.storage.service import StorageService
from bordereaux.treaty.derivation import rates_by_currency

_NUMERIC_SHARE = 0.8


async def _run(db: AsyncSession, run_id: UUID) -> ProcessingRun:
    run = await db.get(ProcessingRun, run_id)
    if run is None:
        raise NotFoundError("Processing run not found", details={"run_id": str(run_id)})
    return run


def _serialize_finding(f: Any) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "check_id": f.check_id,
        "category": f.category,
        "affected_row_number": f.affected_row_number,
        "affected_field": f.affected_field,
        "evidence": f.evidence or {},
        "expected_value": str(f.expected_value) if f.expected_value is not None else None,
        "observed_value": str(f.observed_value) if f.observed_value is not None else None,
        "variance": str(f.variance) if f.variance is not None else None,
        "message": f.message,
        "severity": f.severity,
        "status": f.status,
        "run_id": str(f.run_id),
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


class _Context:
    """Everything the detectors and the revised view read, loaded once per request."""

    def __init__(self) -> None:
        self.run: ProcessingRun
        self.dataset: dict[str, Any]
        self.treaty: TreatyRates
        self.figures: TreatyFigures | None
        self.series: d.Series
        self.base_roles: dict[str, MoneyRole]
        self.fx_by_currency: dict[str, Decimal]


async def _load(db: AsyncSession, storage: StorageService, run_id: UUID) -> _Context:
    ctx = _Context()
    ctx.run = await _run(db, run_id)
    ctx.dataset = await load_standardized_dataset(storage, run_id)
    ctx.figures = await db.get(TreatyFigures, ctx.run.treaty_figures_id) if ctx.run.treaty_figures_id else None
    ctx.treaty = treaty_rates_of(ctx.figures)
    ctx.fx_by_currency = {}
    if ctx.figures is not None:
        ctx.fx_by_currency = rates_by_currency(ctx.figures.settlement_currency, ctx.figures.fx_rates)
        if ctx.figures.fx_rate is not None and ctx.figures.currency:
            ctx.fx_by_currency.setdefault(ctx.figures.currency.upper(), ctx.figures.fx_rate)

    load_schema_definition()  # populates MONEY_ROLE_BY_FIELD
    valid = {r.name: r for r in MoneyRole}
    rows = ctx.dataset["rows"]
    present = list(ctx.dataset["fields"])
    approved = (
        await db.execute(select(ApprovedMapping).where(ApprovedMapping.mapping_set_id == ctx.run.mapping_set_id))
    ).scalars().all()
    source_of = {a.target_field: a.source_column for a in approved}

    ctx.base_roles = {
        f: valid[MONEY_ROLE_BY_FIELD[f]] for f in present if MONEY_ROLE_BY_FIELD.get(f) in valid
    }

    def parsed(field_name: str) -> tuple[Decimal | None, ...]:
        return tuple(try_parse_decimal(r.get(field_name))[0] for r in rows)

    columns: dict[str, d.ColumnInfo] = {}
    for f in present:
        role = ctx.base_roles.get(f)
        if role is None:
            # Unmapped (extra_*) numeric columns can only matter when their header says "net" (D3/D4).
            if not f.startswith("extra_"):
                continue
            non_blank = [r.get(f) for r in rows if r.get(f) not in (None, "")]
            if not non_blank or sum(1 for v in non_blank if try_parse_decimal(v)[1]) / len(non_blank) < _NUMERIC_SHARE:
                continue
        columns[f] = d.ColumnInfo(f, source_of.get(f, f), role, parsed(f))

    ctx.series = d.Series(
        row_numbers=tuple(r["_source_row_number"] for r in rows),
        columns=columns,
        sum_insured=parsed("sum_insured") if "sum_insured" in present else tuple(None for _ in rows),
        class_of_business=tuple(
            str(r.get("class_of_business")).strip().lower() if r.get("class_of_business") else None for r in rows
        ),
    )
    return ctx


def _serialize_suggestion(s: d.RenameSuggestion, decision: ColumnRenameDecision | None) -> dict[str, Any]:
    return {
        "key": s.key,
        "rule_id": s.rule_id,
        "field": s.field,
        "source_column": s.source_column,
        "current_meaning": s.current_meaning,
        "suggested_role": s.suggested_role,
        "suggested_label": d.label_for(s.suggested_role),
        "flag": s.flag,
        "renamable": s.renamable,
        "agreement": str(s.agreement.quantize(Decimal("0.0001"))),
        "rows_evaluated": s.rows_evaluated,
        "evidence_text": s.evidence_text,
        "evidence": s.evidence,
        "accepted": decision is not None,
        "decided_at": decision.created_at.isoformat() if decision is not None and decision.created_at else None,
    }


async def _decisions(db: AsyncSession, run_id: UUID) -> list[ColumnRenameDecision]:
    stmt = (
        select(ColumnRenameDecision)
        .where(ColumnRenameDecision.run_id == run_id)
        .order_by(ColumnRenameDecision.created_at, ColumnRenameDecision.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_workspace(db: AsyncSession, storage: StorageService, run_id: UUID) -> dict[str, Any]:
    ctx = await _load(db, storage, run_id)
    analysis = d.analyse(ctx.series, ctx.treaty, MCRConfig())
    decisions = {x.suggestion_key: x for x in await _decisions(db, run_id)}
    # A decision whose suggestion no longer appears (e.g. treaty figures were redone) is not applied.
    live = [x for key, x in decisions.items() if key in {s.key for s in analysis.suggestions}]

    originals = [_serialize_finding(f) for f in await list_discrepancies(db, run_id)]
    has_relations = (
        await db.execute(select(MoneyRelation.id).where(MoneyRelation.run_id == run_id).limit(1))
    ).first() is not None
    revised = revised_discrepancies(
        str(run_id),
        ctx.dataset["rows"],
        ctx.base_roles,
        [AcceptedRename(x.rule_id, x.target_field, x.new_meaning) for x in live],
        ctx.treaty,
        MCRConfig(),
        ctx.fx_by_currency,
        originals,
        has_relations,
    )
    return {
        "editable": ctx.run.status == ProcessingRunStatus.DISCREPANCY_REVIEW_PENDING.value,
        "suggestions": [_serialize_suggestion(s, decisions.get(s.key)) for s in analysis.suggestions],
        "not_evaluated": [{"rule_id": n.rule_id, "field": n.field, "reason": n.reason} for n in analysis.not_evaluated],
        "accepted_count": len(live),
        "revised": revised,
    }


def _assert_open(run: ProcessingRun) -> None:
    if run.status != ProcessingRunStatus.DISCREPANCY_REVIEW_PENDING.value:
        raise StateError(
            f"Column renamings can only be changed while discrepancies are under review (current: {run.status}).",
            code="RENAMING_LOCKED",
            details={"run_id": str(run.id), "status": run.status},
        )


async def accept_renames(
    db: AsyncSession,
    storage: StorageService,
    run_id: UUID,
    actor_id: UUID,
    keys: list[str],
    note: str | None = None,
) -> dict[str, Any]:
    ctx = await _load(db, storage, run_id)
    _assert_open(ctx.run)
    if not keys:
        raise ValidationError("Choose at least one suggested renaming.")
    by_key = {s.key: s for s in d.analyse(ctx.series, ctx.treaty, MCRConfig()).suggestions}
    existing = {x.suggestion_key for x in await _decisions(db, run_id)}
    audit = AuditService(db)
    for key in dict.fromkeys(keys):
        s = by_key.get(key)
        if s is None:
            raise ValidationError(f"'{key}' is not a current renaming suggestion for this run.")
        if not s.renamable:
            raise ValidationError(f"'{s.source_column}' is ambiguous - it needs a person to confirm its meaning, not a rename.")
        if key in existing:
            continue
        # One column takes one new meaning.
        if any(x.target_field == s.field for x in await _decisions(db, run_id)):
            raise StateError(f"'{s.source_column}' already has an accepted renaming.", code="ALREADY_RENAMED")
        db.add(
            ColumnRenameDecision(
                run_id=run_id,
                suggestion_key=key,
                rule_id=s.rule_id,
                target_field=s.field,
                source_column=s.source_column,
                original_meaning=s.current_meaning,
                new_meaning=s.suggested_role,
                evidence={"text": s.evidence_text, **s.evidence, "agreement": str(s.agreement)},
                note=note,
                decided_by=actor_id,
            )
        )
        await db.flush()
        await audit.log(
            event_type="column_rename.accepted",
            target_type="ProcessingRun",
            target_id=run_id,
            action=AuditAction.UPDATE,
            actor_id=actor_id,
            actor_type=ActorType.USER,
            reason=note,
            after_state={"field": s.field, "from": s.current_meaning, "to": s.suggested_role, "rule": s.rule_id},
            project_id=ctx.run.project_id,
        )
    await db.commit()
    return await get_workspace(db, storage, run_id)


async def revert_rename(
    db: AsyncSession, storage: StorageService, run_id: UUID, actor_id: UUID, key: str
) -> dict[str, Any]:
    run = await _run(db, run_id)
    _assert_open(run)
    decision = next((x for x in await _decisions(db, run_id) if x.suggestion_key == key), None)
    if decision is None:
        raise NotFoundError("No accepted renaming with that key", details={"key": key})
    await db.delete(decision)
    await db.flush()
    await AuditService(db).log(
        event_type="column_rename.reverted",
        target_type="ProcessingRun",
        target_id=run_id,
        action=AuditAction.UPDATE,
        actor_id=actor_id,
        actor_type=ActorType.USER,
        before_state={"field": decision.target_field, "to": decision.new_meaning, "rule": decision.rule_id},
        project_id=run.project_id,
    )
    await db.commit()
    return await get_workspace(db, storage, run_id)
