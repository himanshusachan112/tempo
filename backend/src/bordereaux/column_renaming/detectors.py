"""Column-level Category D detection (rule doc §5, MCR-D1 .. D5) -> rename suggestions.

``money_rules.engine`` / ``money_rules.traps`` answer the same questions per *row*
for the discrepancy table. Here the question is asked of a whole *column*: "is this
column's label wrong?". The answer is a ``RenameSuggestion`` the reviewer may accept.

Pure, deterministic ``Decimal`` arithmetic (ADR-002): nothing here touches the database
or an LLM, and nothing is applied until a person accepts it.

* D1 gross-is-actually-ceded   needs cess%            -> ``ceded_premium``
* D2 commission-bundles-brokerage needs comm% + brok% -> ``commission_plus_brokerage``
* D3 ambiguous net              needs tax              -> ``net_of_commission`` / ``net_premium``
* D4 out-of-scope "Net"         (fails A1..A5)         -> flagged, no premium meaning assigned
* D5 our-share labelled "100%"  needs order%           -> the our-share version of the field
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from bordereaux.money_rules.config import MCRConfig
from bordereaux.money_rules.engine import detect_commission_bundles_brokerage, detect_our_share_mislabelled
from bordereaux.money_rules.rules import approx_equal
from bordereaux.money_rules.traps import detect_gross_is_ceded_rows
from bordereaux.money_rules.types import MoneyRole, MoneyRow, TreatyRates

R = MoneyRole

# Suggested meanings (``RenameSuggestion.suggested_role``).
CEDED_PREMIUM = "ceded_premium"
COMMISSION_PLUS_BROKERAGE = "commission_plus_brokerage"
NET_OF_COMMISSION = "net_of_commission"
NET_PREMIUM = "net_premium"
OUR_SHARE_OF: dict[MoneyRole, str] = {
    R.GROSS: "gross_premium_our_share",
    R.COMMISSION: "commission_our_share",
    R.NET: "net_premium_our_share",
    R.FINAL_NET: "final_net_our_share",
}

# Flags shown to the reviewer.
MEANING_CONFLICT = "MEANING_CONFLICT"
BUNDLED = "BUNDLED"
PARTIAL_NET = "PARTIAL_NET"
AMBIGUOUS = "AMBIGUOUS"

_NET_HEADER = re.compile(r"\bnet\b", re.I)
_HUNDRED_PERCENT = re.compile(r"100\s*%")

_LABELS = {
    CEDED_PREMIUM: "Ceded Premium",
    COMMISSION_PLUS_BROKERAGE: "Commission + Brokerage",
    NET_OF_COMMISSION: "Net of Commission",
    NET_PREMIUM: "Net Premium",
    "gross_premium_our_share": "Gross Premium (Our Share)",
    "commission_our_share": "Commission (Our Share)",
    "net_premium_our_share": "Net Premium (Our Share)",
    "final_net_our_share": "Final Net (Our Share)",
}


def label_for(role: str | None) -> str | None:
    return _LABELS.get(role) if role else None


@dataclass(frozen=True)
class ColumnInfo:
    """One numeric column of the standardized file, aligned by position with every other."""

    field: str
    source_column: str
    role: MoneyRole | None
    values: tuple[Decimal | None, ...]


@dataclass(frozen=True)
class RenameSuggestion:
    key: str
    rule_id: str
    field: str
    source_column: str
    current_meaning: str
    suggested_role: str | None
    flag: str
    agreement: Decimal
    rows_evaluated: int
    evidence_text: str
    evidence: dict = field(default_factory=dict)

    @property
    def renamable(self) -> bool:
        """Ambiguous columns are routed to review, never renamed by guesswork (D3/D4)."""
        return self.suggested_role is not None


@dataclass(frozen=True)
class NotEvaluated:
    rule_id: str
    field: str
    reason: str


@dataclass
class Analysis:
    suggestions: list[RenameSuggestion] = field(default_factory=list)
    not_evaluated: list[NotEvaluated] = field(default_factory=list)


@dataclass(frozen=True)
class Series:
    """Everything the detectors read, by row position."""

    row_numbers: tuple[int, ...]
    columns: dict[str, ColumnInfo]
    sum_insured: tuple[Decimal | None, ...]
    class_of_business: tuple[str | None, ...]

    def by_role(self, role: MoneyRole) -> ColumnInfo | None:
        return next((c for c in self.columns.values() if c.role is role), None)


def _share(hits: int, total: int) -> Decimal:
    return Decimal(hits) / Decimal(total) if total else Decimal(0)


def _pct(value: Decimal) -> str:
    return f"{value * 100:.0f}%"


def _money_rows(series: Series) -> list[MoneyRow]:
    rows: list[MoneyRow] = []
    for i, number in enumerate(series.row_numbers):
        values = {c.role: c.values[i] for c in series.columns.values() if c.role is not None}
        rows.append(MoneyRow(number, values))
    return rows


def _in_effect(agreement: Decimal, evaluated: int, cfg: MCRConfig) -> bool:
    return evaluated >= cfg.min_rows_for_resolution and agreement >= cfg.row_agreement_threshold


# --------------------------------------------------------------------------- D1


def _detect_d1(series: Series, treaty: TreatyRates, cfg: MCRConfig, out: Analysis) -> None:
    gross = series.by_role(R.GROSS)
    if gross is None:
        return
    if treaty.cession_pct is None or not (0 < treaty.cession_pct < 1):
        out.not_evaluated.append(NotEvaluated("MCR-D1", gross.field, "Needs the cession rate (cess%) from the treaty figures."))
        return
    rows = _money_rows(series)
    si = {n: v for n, v in zip(series.row_numbers, series.sum_insured) if v is not None}
    groups = {n: g for n, g in zip(series.row_numbers, series.class_of_business) if g}
    evaluable = sum(1 for v, s in zip(gross.values, series.sum_insured) if v and v > 0 and s and s > 0)
    traps = detect_gross_is_ceded_rows(rows, si, groups, treaty, cfg)
    agreement = _share(len(traps), evaluable)
    if not _in_effect(agreement, evaluable, cfg):
        return
    out.suggestions.append(
        RenameSuggestion(
            key=f"MCR-D1:{gross.field}",
            rule_id="MCR-D1",
            field=gross.field,
            source_column=gross.source_column,
            current_meaning="gross_premium",
            suggested_role=CEDED_PREMIUM,
            flag=MEANING_CONFLICT,
            agreement=agreement,
            rows_evaluated=evaluable,
            evidence_text=(
                f"Column equals cess% of the implied gross in {_pct(agreement)} of rows and is far below Sum Insured "
                "versus peer gross columns - it is the ceded share, not the full gross."
            ),
            evidence={
                "cession_pct": str(treaty.cession_pct),
                "rows_matching": len(traps),
                "example_implied_gross": str(traps[0].expected_value) if traps and traps[0].expected_value else None,
            },
        )
    )


# --------------------------------------------------------------------------- D2


def _detect_d2(series: Series, treaty: TreatyRates, cfg: MCRConfig, out: Analysis) -> None:
    comm, gross = series.by_role(R.COMMISSION), series.by_role(R.GROSS)
    if comm is None or gross is None:
        return
    if treaty.commission_pct is None or treaty.brokerage_pct is None or treaty.brokerage_pct == 0:
        out.not_evaluated.append(
            NotEvaluated("MCR-D2", comm.field, "Needs both the commission rate and a non-zero brokerage rate.")
        )
        return
    pairs = [(c, g) for c, g in zip(comm.values, gross.values) if c is not None and g is not None and g != 0]
    cols, grosses = [c for c, _ in pairs], [g for _, g in pairs]
    result = detect_commission_bundles_brokerage(cols, grosses, treaty, cfg)
    if not result.detected:
        return
    combined = treaty.commission_pct + treaty.brokerage_pct
    agreement = _share(sum(1 for c, g in pairs if approx_equal(c, g * combined, cfg.tolerance)), len(pairs))
    out.suggestions.append(
        RenameSuggestion(
            key=f"MCR-D2:{comm.field}",
            rule_id="MCR-D2",
            field=comm.field,
            source_column=comm.source_column,
            current_meaning="commission",
            suggested_role=COMMISSION_PLUS_BROKERAGE,
            flag=BUNDLED,
            agreement=agreement,
            rows_evaluated=len(pairs),
            evidence_text=(
                f"Column equals comm% + brok% of gross ({_pct(combined)}) in {_pct(agreement)} of rows, exceeding "
                "commission alone - it bundles commission and brokerage."
            ),
            evidence={
                "commission_pct": str(treaty.commission_pct),
                "brokerage_pct": str(treaty.brokerage_pct),
                "combined_pct": str(combined),
            },
        )
    )


# ------------------------------------------------------------------ D3 and D4


def _net_candidates(series: Series) -> list[ColumnInfo]:
    return [c for c in series.columns.values() if c.role is R.NET or (c.role is None and _NET_HEADER.search(c.source_column))]


def _identity_rates(
    col: ColumnInfo, gross: ColumnInfo, comm: ColumnInfo, tax: list[Decimal | None], cfg: MCRConfig
) -> tuple[Decimal, Decimal, int, bool]:
    """(A1 agreement, A3 agreement, rows, tax_is_zero) for ``col`` as a net figure."""
    rows = [
        (g, c, t, n)
        for g, c, t, n in zip(gross.values, comm.values, tax, col.values)
        if g is not None and c is not None and t is not None and n is not None
    ]
    a1 = sum(1 for g, c, t, n in rows if approx_equal(g - c - t, n, cfg.tolerance))
    a3 = sum(1 for g, c, _t, n in rows if approx_equal(g - c, n, cfg.tolerance))
    return _share(a1, len(rows)), _share(a3, len(rows)), len(rows), all(t == 0 for _g, _c, t, _n in rows)


def _fn_agreement(series: Series, col: ColumnInfo, cfg: MCRConfig) -> Decimal:
    """Best agreement of ``col`` as a final net (A2: G-C-B-T, A5: N-B) - the A-identities D4 also tries."""
    gross, comm, brok = series.by_role(R.GROSS), series.by_role(R.COMMISSION), series.by_role(R.BROKERAGE)
    tax, net = series.by_role(R.TAX), series.by_role(R.NET)
    best = Decimal(0)
    if gross and comm and brok and tax:
        rows = [v for v in zip(gross.values, comm.values, brok.values, tax.values, col.values) if None not in v]
        best = max(best, _share(sum(1 for g, c, b, t, n in rows if approx_equal(g - c - b - t, n, cfg.tolerance)), len(rows)))
    if net and brok and net.field != col.field:
        rows = [v for v in zip(net.values, brok.values, col.values) if None not in v]
        best = max(best, _share(sum(1 for n, b, f in rows if approx_equal(n - b, f, cfg.tolerance)), len(rows)))
    return best


def _detect_d3_d4(series: Series, treaty: TreatyRates, cfg: MCRConfig, out: Analysis) -> None:
    gross, comm, tax_col = series.by_role(R.GROSS), series.by_role(R.COMMISSION), series.by_role(R.TAX)
    candidates = _net_candidates(series)
    if not candidates or gross is None or comm is None:
        return
    if tax_col is not None:
        tax: list[Decimal | None] = list(tax_col.values)
    elif treaty.tax_pct is not None:
        tax = [g * treaty.tax_pct if g is not None else None for g in gross.values]
    else:
        for c in candidates:
            out.not_evaluated.append(NotEvaluated("MCR-D3", c.field, "Needs a tax column or the tax rate to tell a partial net from a true net."))
        return

    measured = {c.field: _identity_rates(c, gross, comm, tax, cfg) for c in candidates}
    true_net = [c for c in candidates if _in_effect(measured[c.field][0], measured[c.field][2], cfg) and not _in_effect(measured[c.field][1], measured[c.field][2], cfg)]

    for c in candidates:
        a1, a3, n, tax_zero = measured[c.field]
        a1_on, a3_on = _in_effect(a1, n, cfg), _in_effect(a3, n, cfg)
        base = {"a1_agreement": str(a1), "a3_agreement": str(a3), "tax_all_zero": tax_zero}
        if a3_on and not a1_on:
            others = [t for t in true_net if t.field != c.field]
            where = f"column {others[0].source_column}" if others else "not present in this file"
            out.suggestions.append(
                RenameSuggestion(
                    key=f"MCR-D3:{c.field}", rule_id="MCR-D3", field=c.field, source_column=c.source_column,
                    current_meaning="net_premium" if c.role is R.NET else "unmapped_net",
                    suggested_role=NET_OF_COMMISSION, flag=PARTIAL_NET, agreement=a3, rows_evaluated=n,
                    evidence_text=(
                        f"This 'Net' equals gross - commission only in {_pct(a3)} of rows; the true net "
                        f"(gross - commission - tax) is {where}."
                    ),
                    evidence={**base, "true_net_field": others[0].field if others else None},
                )
            )
        elif a1_on and not a3_on:
            if c.role is R.NET:
                continue  # already read as the true net: nothing to rename
            out.suggestions.append(
                RenameSuggestion(
                    key=f"MCR-D3:{c.field}", rule_id="MCR-D3", field=c.field, source_column=c.source_column,
                    current_meaning="unmapped_net", suggested_role=NET_PREMIUM, flag=MEANING_CONFLICT, agreement=a1,
                    rows_evaluated=n,
                    evidence_text=f"This 'Net' equals gross - commission - tax in {_pct(a1)} of rows - it is the true net premium.",
                    evidence=base,
                )
            )
        elif a1_on and a3_on:
            out.suggestions.append(
                RenameSuggestion(
                    key=f"MCR-D3:{c.field}", rule_id="MCR-D3", field=c.field, source_column=c.source_column,
                    current_meaning="net_premium" if c.role is R.NET else "unmapped_net", suggested_role=None,
                    flag=AMBIGUOUS, agreement=a1, rows_evaluated=n,
                    evidence_text=(
                        "Tax is zero, so gross - commission and gross - commission - tax give the same figure; this "
                        "'Net' cannot be told apart - confirm which it is."
                    ),
                    evidence=base,
                )
            )
        else:
            best = max(a1, a3, _fn_agreement(series, c, cfg))
            if best < cfg.discrepancy_agreement_threshold and (c.role is R.NET or _NET_HEADER.search(c.source_column)):
                out.suggestions.append(
                    RenameSuggestion(
                        key=f"MCR-D4:{c.field}", rule_id="MCR-D4", field=c.field, source_column=c.source_column,
                        current_meaning="net_premium" if c.role is R.NET else "unmapped_net", suggested_role=None,
                        flag=AMBIGUOUS, agreement=best, rows_evaluated=n,
                        evidence_text=(
                            "This 'Net' does not satisfy any premium deduction identity (A1-A5), so no premium meaning "
                            "was assigned - it may be a claims-side net. Confirm what it represents."
                        ),
                        evidence={**base, "best_identity_agreement": str(best)},
                    )
                )


# --------------------------------------------------------------------------- D5


def _detect_d5(series: Series, treaty: TreatyRates, cfg: MCRConfig, out: Analysis) -> None:
    order = treaty.order_pct
    for col in series.columns.values():
        if col.role not in OUR_SHARE_OF or not _HUNDRED_PERCENT.search(col.source_column):
            continue
        if order is None or not (0 < order < 1):
            out.not_evaluated.append(NotEvaluated("MCR-D5", col.field, "Needs the order % (below 100%) from the treaty figures."))
            continue
        larger: list[tuple[str, list[Decimal | None]]] = [
            (other.source_column, list(other.values)) for other in series.columns.values() if other.field != col.field
        ]
        if col.role is R.GROSS and treaty.expected_si_rate:
            larger.append(("the gross implied by Sum Insured", [s * treaty.expected_si_rate if s else None for s in series.sum_insured]))
        best: tuple[Decimal, int, str] | None = None
        for name, other_values in larger:
            pairs = [(v, o) for v, o in zip(col.values, other_values) if v is not None and o is not None and o != 0]
            if not pairs:
                continue
            res = detect_our_share_mislabelled([v for v, _ in pairs], [o for _, o in pairs], treaty, cfg)
            if res.detected:
                agreement = _share(sum(1 for v, o in pairs if approx_equal(v, o * order, cfg.tolerance)), len(pairs))
                if best is None or agreement > best[0]:
                    best = (agreement, len(pairs), name)
        if best is None:
            continue
        agreement, n, against = best
        role = OUR_SHARE_OF[col.role]
        out.suggestions.append(
            RenameSuggestion(
                key=f"MCR-D5:{col.field}", rule_id="MCR-D5", field=col.field, source_column=col.source_column,
                current_meaning=f"{col.role.name.lower()}_100", suggested_role=role, flag=MEANING_CONFLICT,
                agreement=agreement, rows_evaluated=n,
                evidence_text=(
                    f"Column equals order% ({_pct(order)}) of {against} in {_pct(agreement)} of rows - it is the our-share "
                    "figure, so its '100%' label is misleading."
                ),
                evidence={"order_pct": str(order), "compared_with": against},
            )
        )


# --------------------------------------------------------------------------- entry


def analyse(series: Series, treaty: TreatyRates, cfg: MCRConfig | None = None) -> Analysis:
    cfg = cfg or MCRConfig()
    out = Analysis()
    _detect_d1(series, treaty, cfg, out)
    _detect_d2(series, treaty, cfg, out)
    _detect_d3_d4(series, treaty, cfg, out)
    _detect_d5(series, treaty, cfg, out)

    # One column cannot be both "the ceded share" (D1) and "the our-share version" (D5) - when the evidence
    # supports both, the treaty rates alone cannot decide, so it goes to a person (rule doc E4).
    clashing = {s.field for s in out.suggestions if s.rule_id == "MCR-D1"} & {
        s.field for s in out.suggestions if s.rule_id == "MCR-D5"
    }
    if clashing:
        kept = [s for s in out.suggestions if s.field not in clashing]
        for name in sorted(clashing):
            first = next(s for s in out.suggestions if s.field == name)
            kept.append(
                RenameSuggestion(
                    key=f"MCR-D1:{name}", rule_id="MCR-D1", field=name, source_column=first.source_column,
                    current_meaning=first.current_meaning, suggested_role=None, flag=AMBIGUOUS,
                    agreement=first.agreement, rows_evaluated=first.rows_evaluated,
                    evidence_text=(
                        "Column fits both the ceded share (cess%) and the our-share figure (order%) - the treaty "
                        "rates cannot tell them apart. Confirm what it represents."
                    ),
                    evidence={"competing_rules": ["MCR-D1", "MCR-D5"]},
                )
            )
        out.suggestions = kept
    out.suggestions.sort(key=lambda s: (s.rule_id, s.field))
    return out
