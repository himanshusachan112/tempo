"""Column-level MCR-D1..D5 detection, using the rule document's worked examples (§5.1 - §5.5)."""

from __future__ import annotations

from decimal import Decimal as D

from bordereaux.column_renaming import detectors as d
from bordereaux.column_renaming.revised import AcceptedRename, remap_roles, revised_discrepancies
from bordereaux.money_rules import MCRConfig, MoneyRole, TreatyRates

R = MoneyRole
N = 8  # comfortably above min_rows_for_resolution


def col(field: str, role: MoneyRole | None, values: list, header: str | None = None) -> d.ColumnInfo:
    return d.ColumnInfo(field, header or field, role, tuple(D(str(v)) if v is not None else None for v in values))


def series(columns: list[d.ColumnInfo], si: list | None = None) -> d.Series:
    n = len(columns[0].values)
    return d.Series(
        row_numbers=tuple(range(1, n + 1)),
        columns={c.field: c for c in columns},
        sum_insured=tuple(D(str(v)) if v is not None else None for v in (si or [None] * n)),
        class_of_business=tuple(None for _ in range(n)),
    )


def only(analysis: d.Analysis, rule: str) -> d.RenameSuggestion:
    found = [s for s in analysis.suggestions if s.rule_id == rule]
    assert len(found) == 1, analysis
    return found[0]


# ------------------------------------------------------------------ D1


def test_d1_gross_that_is_really_the_ceded_share() -> None:
    # §5.1: Prem 4,000, SI 1,000,000, expected rate 1% -> implied gross 10,000, cess 40%, comm 25% of Prem.
    s = series(
        [col("gross_premium", R.GROSS, [4000] * N), col("commission", R.COMMISSION, [1000] * N)],
        si=[1_000_000] * N,
    )
    treaty = TreatyRates(cession_pct=D("0.40"), commission_pct=D("0.25"), expected_si_rate=D("0.01"))
    sug = only(d.analyse(s, treaty), "MCR-D1")
    assert sug.suggested_role == d.CEDED_PREMIUM and sug.flag == d.MEANING_CONFLICT and sug.renamable
    assert "ceded share, not the full gross" in sug.evidence_text and sug.agreement == 1


def test_d1_needs_the_cession_rate() -> None:
    s = series([col("gross_premium", R.GROSS, [4000] * N)], si=[1_000_000] * N)
    out = d.analyse(s, TreatyRates(expected_si_rate=D("0.01")))
    assert not out.suggestions
    assert [(n.rule_id, n.field) for n in out.not_evaluated] == [("MCR-D1", "gross_premium")]


def test_d1_true_gross_is_left_alone() -> None:
    s = series([col("gross_premium", R.GROSS, [10000] * N)], si=[1_000_000] * N)
    assert not d.analyse(s, TreatyRates(cession_pct=D("0.40"), expected_si_rate=D("0.01"))).suggestions


# ------------------------------------------------------------------ D2


def test_d2_commission_bundles_brokerage() -> None:
    s = series([col("gross_premium", R.GROSS, [10000] * N), col("commission", R.COMMISSION, [3000] * N)])
    treaty = TreatyRates(commission_pct=D("0.25"), brokerage_pct=D("0.05"))
    sug = only(d.analyse(s, treaty), "MCR-D2")
    assert sug.suggested_role == d.COMMISSION_PLUS_BROKERAGE and sug.flag == d.BUNDLED
    assert "bundles commission and brokerage" in sug.evidence_text


def test_d2_pure_commission_is_not_flagged() -> None:
    s = series([col("gross_premium", R.GROSS, [10000] * N), col("commission", R.COMMISSION, [2500] * N)])
    assert not d.analyse(s, TreatyRates(commission_pct=D("0.25"), brokerage_pct=D("0.05"))).suggestions


def test_d2_needs_both_rates() -> None:
    s = series([col("gross_premium", R.GROSS, [10000] * N), col("commission", R.COMMISSION, [3000] * N)])
    out = d.analyse(s, TreatyRates(commission_pct=D("0.25")))
    assert not out.suggestions and "MCR-D2" in {n.rule_id for n in out.not_evaluated}


# ------------------------------------------------------------------ D3 / D4


def _net_file(net: list, net_due: list | None = None, tax: int = 1200) -> d.Series:
    cols = [
        col("gross_premium", R.GROSS, [10000] * N),
        col("commission", R.COMMISSION, [2500] * N),
        col("tax", R.TAX, [tax] * N),
        col("net_premium", R.NET, net, header="Net"),
    ]
    if net_due is not None:
        cols.append(col("extra_net_due", None, net_due, header="Net Due"))
    return series(cols)


def test_d3_partial_net_and_true_net_are_each_resolved() -> None:
    out = d.analyse(_net_file([7500] * N, [6300] * N), TreatyRates())
    partial = next(s for s in out.suggestions if s.field == "net_premium")
    assert partial.field == "net_premium" and partial.suggested_role == d.NET_OF_COMMISSION
    assert "the true net (gross - commission - tax) is column Net Due" in partial.evidence_text
    # Net Due is the true net but was not mapped: it is offered the net_premium meaning too.
    assert {s.field: s.suggested_role for s in out.suggestions} == {
        "net_premium": d.NET_OF_COMMISSION,
        "extra_net_due": d.NET_PREMIUM,
    }


def test_d3_true_net_in_the_net_role_needs_no_rename() -> None:
    assert not d.analyse(_net_file([6300] * N), TreatyRates()).suggestions


def test_d3_single_net_with_zero_tax_is_ambiguous_not_guessed() -> None:
    sug = only(d.analyse(_net_file([7500] * N, tax=0), TreatyRates()), "MCR-D3")
    assert sug.flag == d.AMBIGUOUS and not sug.renamable


def test_d4_net_that_fits_no_premium_identity_is_flagged_for_review() -> None:
    sug = only(d.analyse(_net_file([1234] * N), TreatyRates()), "MCR-D4")
    assert sug.flag == d.AMBIGUOUS and sug.suggested_role is None and not sug.renamable


# ------------------------------------------------------------------ D5


def test_d5_hundred_percent_label_on_our_share_figures() -> None:
    # §5.5: both columns 20,000, order 20%, SI 1,000,000 at 10% => implied 100,000.
    s = series(
        [
            col("gross_premium", R.GROSS, [20000] * N, header="GWP (100%)"),
            col("extra_gwp_our", None, [20000] * N, header="GWP (Our)"),
        ],
        si=[1_000_000] * N,
    )
    treaty = TreatyRates(order_pct=D("0.20"), expected_si_rate=D("0.10"))
    sug = only(d.analyse(s, treaty), "MCR-D5")
    assert sug.field == "gross_premium" and sug.suggested_role == "gross_premium_our_share"
    assert "'100%' label is misleading" in sug.evidence_text


def test_d5_only_applies_to_hundred_percent_labels() -> None:
    s = series([col("gross_premium", R.GROSS, [20000] * N, header="GWP")], si=[1_000_000] * N)
    assert not d.analyse(s, TreatyRates(order_pct=D("0.20"), expected_si_rate=D("0.10"))).suggestions


def test_d1_and_d5_on_the_same_column_become_one_ambiguous_flag() -> None:
    s = series([col("gross_premium", R.GROSS, [4000] * N, header="GWP (100%)")], si=[1_000_000] * N)
    treaty = TreatyRates(cession_pct=D("0.40"), order_pct=D("0.40"), expected_si_rate=D("0.01"))
    out = d.analyse(s, treaty)
    assert len(out.suggestions) == 1 and not out.suggestions[0].renamable


# ------------------------------------------------------------------ revised view


def _row(n: int, **v: object) -> dict:
    return {"_source_row_number": n, **v}


def test_revised_view_clears_the_findings_the_renaming_explains() -> None:
    rows = [_row(i, gross_premium="4000", commission="1000") for i in range(1, N + 1)]
    original = [
        {"id": "x", "check_id": "MCR-D1", "category": "TRAP", "affected_row_number": 1, "affected_field": None,
         "evidence": {}, "message": "m"},
        {"id": "y", "check_id": "MCR-B1", "category": "TREATY_RATIO", "affected_row_number": 1, "affected_field": None,
         "evidence": {}, "message": "m"},
    ]
    treaty = TreatyRates(cession_pct=D("0.40"), commission_pct=D("0.25"))
    base = {"gross_premium": R.GROSS, "commission": R.COMMISSION}
    out = revised_discrepancies(
        "run", rows, base, [AcceptedRename("MCR-D1", "gross_premium", d.CEDED_PREMIUM)], treaty, MCRConfig(), {}, original, False
    )
    assert out["active"] and out["findings"] == []  # B5 holds: commission is 25% of the ceded figure
    assert out["summary"] == {"original_total": 2, "revised_total": 0, "cleared": 2, "new": 0}


def test_revised_view_is_inactive_without_decisions() -> None:
    out = revised_discrepancies("run", [], {}, [], TreatyRates(), MCRConfig(), {}, [{"check_id": "MCR-B1"}], False)
    assert out == {"active": False, "findings": [], "summary": {"original_total": 1, "revised_total": 1, "cleared": 0, "new": 0}}


def test_remap_roles_net_of_commission_frees_the_net_role_for_the_true_net() -> None:
    roles = remap_roles(
        {"net_premium": R.NET},
        [AcceptedRename("MCR-D3", "extra_net_due", d.NET_PREMIUM), AcceptedRename("MCR-D3", "net_premium", d.NET_OF_COMMISSION)],
    )
    assert roles == {"extra_net_due": R.NET}
