"""Revised discrepancies: the money-column checks re-run with the accepted renamings applied.

Nothing here is stored or written back. The original findings stay exactly as the financial
checks produced them; this module only answers "what would the discrepancies be if these
columns meant what the reviewer accepted?" so the UI can show it next to the original table.

How each accepted meaning changes the re-run (all deterministic):

* ``ceded_premium``              the column leaves the gross role and takes the ceded-premium role
* ``<field>_our_share``          the column takes the matching our-share role
* ``commission_plus_brokerage``  commission used is ``column - gross x brok%`` (the pure commission),
                                 so brokerage is not deducted twice
* ``net_of_commission``          the column leaves the net role and is checked as ``G - C`` (MCR-A3)
* ``net_premium``                the column takes the net role
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from bordereaux.column_renaming import detectors as d
from bordereaux.money_rules import MCRConfig, MoneyRole, MoneyRow, TreatyRates, find_discrepancies
from bordereaux.money_rules.engine import _ROLE_RULE_MAP, DiscrepancyCandidate
from bordereaux.money_rules.rules import RuleOutcome, approx_equal, rule_a3

R = MoneyRole

_SHARE_ROLE = {
    "gross_premium_our_share": R.OUR_SHARE,
    "commission_our_share": R.COMMISSION_OUR_SHARE,
    "net_premium_our_share": R.NET_OUR_SHARE,
    "final_net_our_share": R.FINAL_NET_OUR_SHARE,
}
#: Built-in per-row identities that a recompute replaces (traps D*, FX, totals F* and sign G1 are carried over).
_RECOMPUTED_PREFIXES = ("MCR-A", "MCR-B", "MCR-C")
_RECOMPUTED_IDS = {"MCR-G2", "MCR-G4"}


@dataclass(frozen=True)
class AcceptedRename:
    rule_id: str
    field: str
    new_meaning: str


def _is_recomputed(rule_id: str) -> bool:
    return rule_id.startswith(_RECOMPUTED_PREFIXES) or rule_id in _RECOMPUTED_IDS


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def remap_roles(base: dict[str, MoneyRole], accepted: Iterable[AcceptedRename]) -> dict[str, MoneyRole]:
    """Field -> role after the renamings. ``net_of_commission`` clears the field's role first so that a
    ``net_premium`` assignment on another column is never overwritten by the order of the list."""
    roles = dict(base)
    ordered = sorted(accepted, key=lambda a: a.new_meaning != d.NET_OF_COMMISSION)
    for a in ordered:
        if a.new_meaning == d.CEDED_PREMIUM:
            roles[a.field] = R.CEDED_PREMIUM
        elif a.new_meaning in _SHARE_ROLE:
            roles[a.field] = _SHARE_ROLE[a.new_meaning]
        elif a.new_meaning == d.NET_OF_COMMISSION:
            roles.pop(a.field, None)
        elif a.new_meaning == d.NET_PREMIUM:
            roles[a.field] = R.NET
    return roles


def _build_rows(
    rows: list[dict[str, Any]],
    roles: dict[str, MoneyRole],
    unbundle: set[str],
    treaty: TreatyRates,
    fx_by_currency: dict[str, Decimal],
) -> list[MoneyRow]:
    out: list[MoneyRow] = []
    for row in rows:
        values: dict[MoneyRole, Decimal | None] = {}
        for field_name, role in roles.items():
            values[role] = _decimal(row.get(field_name))
        gross = values.get(R.GROSS)
        for field_name in unbundle:
            col = _decimal(row.get(field_name))
            if col is not None and gross is not None and treaty.brokerage_pct is not None:
                values[R.COMMISSION] = col - gross * treaty.brokerage_pct
        ccy = str(row.get("original_currency") or "").strip().upper()
        out.append(MoneyRow(row["_source_row_number"], values, fx_rate=fx_by_currency.get(ccy)))
    return out


def _finding(i: int, run_id: str, c: Any, source: str) -> dict[str, Any]:
    return {
        "id": f"revised-{i}",
        "run_id": run_id,
        "check_id": c.rule_id,
        "category": c.category,
        "severity": "ERROR",
        "status": "UNRESOLVED",
        "affected_row_number": c.row_number,
        "affected_field": (c.evidence or {}).get("affected_field"),
        "expected_value": str(c.expected_value) if c.expected_value is not None else None,
        "observed_value": str(c.observed_value) if c.observed_value is not None else None,
        "variance": str(c.variance) if c.variance is not None else None,
        "evidence": {**(c.evidence or {}), "revised_source": source},
        "message": c.message,
        "source": source,
    }


def revised_discrepancies(
    run_id: str,
    rows: list[dict[str, Any]],
    base_roles: dict[str, MoneyRole],
    accepted: list[AcceptedRename],
    treaty: TreatyRates,
    cfg: MCRConfig,
    fx_by_currency: dict[str, Decimal],
    originals: list[dict[str, Any]],
    has_relations: bool,
) -> dict[str, Any]:
    """The revised list plus a summary against ``originals`` (serialized original findings)."""
    if not accepted:
        n = len(originals)
        return {
            "active": False,
            "findings": [],
            "summary": {"original_total": n, "revised_total": n, "cleared": 0, "new": 0},
        }

    roles = remap_roles(base_roles, accepted)
    unbundle = {a.field for a in accepted if a.new_meaning == d.COMMISSION_PLUS_BROKERAGE}
    money_rows = _build_rows(rows, roles, unbundle, treaty, fx_by_currency)

    recomputed = [c for c in find_discrepancies(money_rows, treaty, cfg) if _is_recomputed(c.rule_id)]
    if has_relations:
        # With declared relations the original run replaced A/B/C with the user's own checks, so only the
        # built-in rules that touch a renamed role are worth re-running (the rest would duplicate REL-*).
        touched_roles = {roles.get(a.field) for a in accepted} | {base_roles.get(a.field) for a in accepted}
        touched_rules = {rid for role in touched_roles if role for rid in _ROLE_RULE_MAP.get(role, [])}
        recomputed = [c for c in recomputed if c.rule_id in touched_rules]

    # net_of_commission columns are checked against the partial-net identity G - C = column.
    for a in accepted:
        if a.new_meaning != d.NET_OF_COMMISSION:
            continue
        partial = _build_rows(rows, {**roles, a.field: R.NET}, unbundle, treaty, fx_by_currency)
        checked = [(m, rule_a3(m, treaty, cfg)) for m in partial]
        evaluated = [x for x in checked if x[1] is not RuleOutcome.NOT_APPLICABLE]
        failing = [m for m, o in evaluated if o is RuleOutcome.FAILS]
        if evaluated and (len(evaluated) - len(failing)) / len(evaluated) >= float(cfg.discrepancy_agreement_threshold):
            for m in failing:
                g, c_, n = m.get(R.GROSS), m.get(R.COMMISSION), m.get(R.NET)
                if None in (g, c_, n):
                    continue
                recomputed.append(
                    DiscrepancyCandidate(
                        rule_id="MCR-A3",
                        category="CORE_DEDUCTION",
                        row_number=m.row_number,
                        expected_value=g - c_,
                        observed_value=n,
                        variance=n - (g - c_),
                        message=f"Net of commission should be G - C = {g - c_}, the file has {n}.",
                        evidence={"affected_field": a.field},
                    )
                )

    renamed_fields = {a.field for a in accepted}
    cleared_rules = {a.rule_id for a in accepted if a.rule_id in ("MCR-D1", "MCR-D2")}
    carried: list[dict[str, Any]] = []
    for f in originals:
        rid = f["check_id"]
        if _is_recomputed(rid) or rid in cleared_rules:
            continue
        ev = f.get("evidence") or {}
        if rid.startswith("REL-") and (
            f.get("affected_field") in renamed_fields or renamed_fields & set((ev.get("inputs") or {}).keys())
        ):
            continue  # a user relation written against the old meaning of a renamed column
        carried.append(f)

    revised: list[dict[str, Any]] = [
        {**f, "id": f"revised-{i}", "source": "carried_over", "evidence": {**(f.get("evidence") or {}), "revised_source": "carried_over"}}
        for i, f in enumerate(carried)
    ]
    start = len(revised)
    revised += [_finding(start + i, run_id, c, "recomputed") for i, c in enumerate(sorted(recomputed, key=lambda c: (c.row_number, c.rule_id)))]
    return {"active": True, "findings": revised, "summary": _summary(originals, revised)}


def _key(f: dict[str, Any]) -> tuple[Any, ...]:
    return (f.get("affected_row_number"), f.get("check_id"))


def _summary(originals: list[dict[str, Any]], revised: list[dict[str, Any]]) -> dict[str, int]:
    before, after = {_key(f) for f in originals}, {_key(f) for f in revised}
    return {
        "original_total": len(originals),
        "revised_total": len(revised),
        "cleared": len(before - after),
        "new": len(after - before),
    }
