"""The human-readable audit report: a Word document built from the stored audit payload.

The JSON audit report stays the machine-readable record. This module only *renders* it, so the Word
file always agrees with the stored artifact and can be regenerated at any time. Every section is a
plain-language heading plus a table; missing sections are skipped rather than shown empty.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MAX_DETAIL_ROWS = 500
_BREAKDOWN_ORDER = ("semantic", "value", "context", "layout", "ai")
_HEADER_FILL = "1F3A5F"
_ZEBRA_FILL = "F3F6FA"
NOT_AVAILABLE = "Not available"


# ----------------------------------------------------------------- formatting helpers


def _clean(value: Any) -> str:
    if value is None or value == "":
        return NOT_AVAILABLE
    return _XML_ILLEGAL.sub("", str(value))


def _words(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def _date(value: Any) -> str:
    if not value:
        return NOT_AVAILABLE
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %Y, %H:%M UTC")
    except ValueError:
        return _clean(value)


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _percent(value: Any, places: int = 0) -> str:
    d = _decimal(value)
    return NOT_AVAILABLE if d is None else f"{d * 100:.{places}f}%"


def _confidence(value: Any) -> str:
    """``0.9500`` -> ``95% (High)``. Bands are stated in the report's legend."""
    d = _decimal(value)
    if d is None:
        return NOT_AVAILABLE
    band = "High" if d >= Decimal("0.90") else "Medium" if d >= Decimal("0.70") else "Low"
    return f"{d * 100:.0f}% ({band})"


def _number(value: Any) -> str:
    d = _decimal(value)
    return NOT_AVAILABLE if d is None else f"{d:,.2f}"


def _short(text: str, limit: int = 220) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "..."


def describe_evidence(items: Any) -> str:
    """Mapping/discrepancy evidence (a dict or a list of dicts) as one readable sentence-like string."""
    if not items:
        return "None recorded"
    if isinstance(items, dict):
        items = [items]
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        label = _words(str(item.get("type") or item.get("description") or "evidence"))
        detail = ", ".join(
            f"{_words(str(k))} {v}"
            for k, v in item.items()
            if k not in ("type", "description") and v not in (None, "", [], {})
        )
        parts.append(f"{label} ({detail})" if detail else label)
    return _short("; ".join(parts), 300)


def _count_by(rows: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key) or "Unspecified")
        counts[k] = counts.get(k, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# ----------------------------------------------------------------- document helpers


def _shade(cell: Any, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _repeat_header(row: Any) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


class _Report:
    def __init__(self) -> None:
        self.doc = Document()
        section = self.doc.sections[0]
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            setattr(section, side, Cm(1.8))
        normal = self.doc.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(10)

    def heading(self, text: str, level: int = 1) -> None:
        self.doc.add_heading(_clean(text), level=level)

    def para(self, text: str, *, italic: bool = False, bold: bool = False, size: int | None = None) -> None:
        p = self.doc.add_paragraph()
        run = p.add_run(_clean(text))
        run.italic, run.bold = italic, bold
        if size:
            run.font.size = Pt(size)
        if italic:
            run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    def table(self, headers: list[str], rows: list[list[Any]], *, widths: list[float] | None = None, font: int = 9) -> None:
        if not rows:
            return
        table = self.doc.add_table(rows=1, cols=len(headers))
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, h in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = ""
            run = cell.paragraphs[0].add_run(_clean(h))
            run.bold, run.font.size = True, Pt(font)
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            _shade(cell, _HEADER_FILL)
        _repeat_header(table.rows[0])
        for n, values in enumerate(rows):
            cells = table.add_row().cells
            for i, v in enumerate(values):
                cells[i].text = ""
                cells[i].paragraphs[0].add_run(v if isinstance(v, str) and v == NOT_AVAILABLE else _clean(v)).font.size = Pt(font)
                if n % 2:
                    _shade(cells[i], _ZEBRA_FILL)
        if widths:
            table.autofit = False
            for row in table.rows:
                for i, w in enumerate(widths):
                    row.cells[i].width = Cm(w)
        self.doc.add_paragraph()

    def key_values(self, pairs: list[tuple[str, Any]]) -> None:
        self.table(["Item", "Value"], [[k, v] for k, v in pairs], widths=[7, 19])

    def bytes(self) -> bytes:
        buf = io.BytesIO()
        self.doc.save(buf)
        return buf.getvalue()


# ----------------------------------------------------------------- sections


def _summary(r: _Report, p: dict[str, Any]) -> None:
    mapping = p.get("mapping") or {}
    approved = mapping.get("approved_mappings") or []
    confidences = [d for a in approved if (d := _decimal(a.get("confidence_total"))) is not None]
    average = sum(confidences) / len(confidences) if confidences else None
    findings = p.get("discrepancies") or []
    overridden = sum(1 for f in findings if f.get("override"))
    cleaning = p.get("cleaning") or {}
    source = p.get("source") or {}
    renamings = p.get("column_renamings") or []

    r.heading("1. Summary", 1)
    r.para(
        f"This report records how the file \"{_clean(source.get('filename'))}\" was processed in run {_clean(p.get('run_id'))}: "
        f"what was cleaned, how its columns were mapped (and how confident the system was), which treaty figures were applied, "
        f"which checks failed and what a reviewer decided. Final status: {_clean(p.get('final_status'))}."
    )
    r.key_values(
        [
            ("Run status", p.get("final_status")),
            ("Report generated", _date(p.get("generated_at"))),
            ("Source file", source.get("filename")),
            ("Rows (after cleaning)", cleaning.get("after_row_count")),
            ("Columns mapped", len(approved)),
            ("Average mapping confidence", _confidence(average) if average is not None else NOT_AVAILABLE),
            ("Discrepancies found", len(findings)),
            ("Discrepancies overridden by a reviewer", overridden),
            ("Discrepancies still unresolved", len(findings) - overridden),
            ("Column renamings accepted", len(renamings)),
            ("AI calls made", len(p.get("ai_activity") or [])),
        ]
    )


def _run_and_source(r: _Report, p: dict[str, Any]) -> None:
    r.heading("2. Run and source file", 1)
    source = p.get("source") or {}
    fmt = p.get("target_format") or {}
    pairs: list[tuple[str, Any]] = [
        ("Run id", p.get("run_id")),
        ("Project id", p.get("project_id")),
        ("Run created", _date(p.get("run_created_at"))),
        ("Report generated", _date(p.get("generated_at"))),
        ("File name", source.get("filename")),
        ("File format", source.get("format")),
        ("File size (bytes)", f"{source['size_bytes']:,}" if isinstance(source.get("size_bytes"), int) else NOT_AVAILABLE),
        ("Checksum (SHA-256)", source.get("checksum")),
        ("Target format", _words(str(fmt.get("mode") or "DEFAULT")).capitalize()),
    ]
    r.key_values(pairs)


def _cleaning(r: _Report, p: dict[str, Any]) -> None:
    c = p.get("cleaning") or {}
    r.heading("3. Data cleaning", 1)
    r.table(
        ["Measure", "Before cleaning", "After cleaning"],
        [
            ["Rows", c.get("before_row_count"), c.get("after_row_count")],
            ["Columns", c.get("before_column_count"), c.get("after_column_count")],
        ],
    )
    r.para(f"Cleaning attempts: {_clean(c.get('attempts'))} (latest: attempt {_clean(c.get('latest_attempt'))}).")
    transformations = c.get("transformations") or []
    if transformations:
        r.heading("Changes made to the data", 2)
        rows = []
        for t in transformations:
            t = t if isinstance(t, dict) else {"description": t}
            rows.append([
                _words(str(t.get("type") or t.get("name") or "change")),
                t.get("column") or t.get("field") or "All",
                t.get("count") if t.get("count") is not None else t.get("affected"),
                t.get("description")
                or describe_evidence({k: v for k, v in t.items() if k not in ("type", "name", "column", "field", "count", "affected")})
                if any(k not in ("type", "name", "column", "field", "count", "affected") for k in t)
                else "-",
            ])
        r.table(["Change", "Column", "Cells/rows affected", "Detail"], rows, widths=[5, 5, 4, 12])
    warnings = c.get("warnings") or []
    if warnings:
        r.heading("Warnings raised during cleaning", 2)
        r.table(
            ["#", "Warning"],
            [[i, w.get("message") or w.get("description") or describe_evidence(w) if isinstance(w, dict) else w] for i, w in enumerate(warnings, 1)],
            widths=[1.5, 24],
        )


def _mapping(r: _Report, p: dict[str, Any]) -> None:
    m = p.get("mapping") or {}
    approved = m.get("approved_mappings") or []
    if not m:
        return
    r.heading("4. Column mapping, confidence and evidence", 1)
    r.para(
        "Each source column was matched to a field of the target format. Confidence is a weighted score from five kinds of "
        "evidence (semantic similarity of the name, value patterns, context, layout and AI support). "
        f"Bands: High is 90% or more, Medium 70-89%, Low below 70%."
    )
    r.key_values(
        [
            ("Mapping finalized", _date(m.get("finalized_at"))),
            ("Schema", m.get("schema_id")),
            ("Required-field coverage", _percent(m.get("coverage_required"))),
            ("Optional-field coverage", _percent(m.get("coverage_optional"))),
            ("Columns approved", len(approved)),
        ]
    )
    rows = []
    for a in approved:
        breakdown = a.get("confidence_breakdown") or {}
        rows.append(
            [
                a.get("source_column"),
                a.get("target_field"),
                _words(str(a.get("origin") or "")),
                _confidence(a.get("confidence_total")),
                *[_percent(breakdown.get(k)) for k in _BREAKDOWN_ORDER],
                describe_evidence(a.get("evidence")),
            ]
        )
    r.table(
        ["Source column", "Mapped to", "How approved", "Overall confidence", "Semantic", "Value", "Context", "Layout", "AI", "Evidence"],
        rows,
        widths=[3.2, 3.2, 2.4, 2.6, 1.6, 1.4, 1.6, 1.4, 1.2, 8],
        font=8,
    )


def _treaty(r: _Report, p: dict[str, Any]) -> None:
    t = p.get("treaty_figures")
    if not t:
        return
    r.heading("5. Treaty figures applied", 1)
    r.table(
        ["Treaty figure", "Value"],
        [
            ["Commission", _percent(t.get("commission_pct"), 2)],
            ["Brokerage", _percent(t.get("brokerage_pct"), 2)],
            ["Tax", _percent(t.get("tax_pct"), 2)],
            ["Cession", _percent(t.get("cession_pct"), 2)],
            ["Order (our share)", _percent(t.get("order_pct"), 2)],
            ["Expected premium rate on sum insured", _percent(t.get("expected_si_rate"), 2)],
            ["Settlement currency", t.get("settlement_currency")],
            ["Single FX rate", t.get("fx_rate")],
        ],
        widths=[10, 10],
    )
    fx = t.get("fx_rates") or []
    if fx:
        r.heading("Currency rates", 2)
        r.table(
            ["Currency", "Rate to settlement currency"],
            [[x.get("currency"), x.get("rate")] for x in fx if isinstance(x, dict)],
            widths=[8, 10],
        )


def _relations(r: _Report, p: dict[str, Any]) -> None:
    rels = p.get("money_relations") or []
    resolutions = p.get("money_column_resolutions") or []
    renames = p.get("column_renamings") or []
    if not (rels or resolutions or renames):
        return
    r.heading("6. Money-column relationships", 1)
    if rels:
        r.heading("Relationships checked", 2)
        r.table(
            ["Relationship", "Expression", "Origin", "Rows checked", "Rows that break it", "Outcome"],
            [
                [
                    x.get("label"),
                    x.get("expression"),
                    _words(str(x.get("origin") or "")),
                    x.get("rows_evaluated"),
                    x.get("rows_failed"),
                    x.get("skipped_reason")
                    or ("Holds on every row checked" if not x.get("rows_failed") else "Some rows break it"),
                ]
                for x in rels
            ],
            widths=[5, 7, 2.6, 2.2, 2.6, 6.5],
        )
    if resolutions:
        r.heading("Unmapped numeric columns tested against the premium identities", 2)
        r.table(
            ["Column", "Looks like", "Ambiguous?", "Rules that matched", "Evidence"],
            [
                [
                    x.get("source_column"),
                    x.get("suggested_field") or "More than one role",
                    "Yes" if x.get("is_ambiguous") else "No",
                    ", ".join(str(m.get("rule_id")) for m in x.get("matched_rules") or [] if isinstance(m, dict)) or "None",
                    describe_evidence(x.get("evidence")),
                ]
                for x in resolutions
            ],
            widths=[4, 4.5, 2.4, 5, 10],
        )
    if renames:
        r.heading("Column renamings accepted by a reviewer", 2)
        r.table(
            ["Column", "Was read as", "Now read as", "Rule", "Confidence", "Evidence", "Accepted"],
            [
                [
                    x.get("source_column"),
                    _words(str(x.get("original_meaning") or "")),
                    _words(str(x.get("new_meaning") or "")),
                    x.get("rule_id"),
                    _confidence((x.get("evidence") or {}).get("agreement")),
                    (x.get("evidence") or {}).get("text") or describe_evidence(x.get("evidence")),
                    _date(x.get("decided_at")),
                ]
                for x in renames
            ],
            widths=[3.5, 3, 3.5, 1.8, 2.6, 8.5, 3.2],
            font=8,
        )


def _discrepancies(r: _Report, p: dict[str, Any]) -> None:
    findings = p.get("discrepancies") or []
    checks = p.get("financial_checks") or {}
    r.heading("7. Financial checks and discrepancies", 1)
    if not findings:
        r.para("No discrepancies were found: every check that applied held on the rows tested.")
        return
    r.para(f"{len(findings)} discrepancies were found. Counts first, then every finding with its evidence.")
    by_status = checks.get("status_counts") or _count_by(findings, "status")
    by_category = checks.get("category_counts") or _count_by(findings, "category")
    r.heading("By category", 2)
    r.table(["Category", "Findings"], [[k, v] for k, v in by_category.items()], widths=[10, 4])
    r.heading("By status", 2)
    r.table(["Status", "Findings"], [[k, v] for k, v in by_status.items()], widths=[10, 4])
    r.heading("By rule", 2)
    r.table(["Rule", "Findings"], [[k, v] for k, v in _count_by(findings, "check_id").items()], widths=[10, 4])

    r.heading("All findings", 2)
    shown = findings[:_MAX_DETAIL_ROWS]
    r.table(
        ["Rule", "Category", "Row", "Field", "Expected", "Observed", "Variance", "Status", "Explanation and evidence"],
        [
            [
                f.get("check_id"),
                f.get("category"),
                f.get("affected_row_number"),
                f.get("affected_field"),
                _number(f.get("expected_value")),
                _number(f.get("observed_value")),
                _number(f.get("variance")),
                f.get("status"),
                _short(f"{_clean(f.get('message'))} | Evidence: {describe_evidence(f.get('evidence'))}", 420),
            ]
            for f in shown
        ],
        widths=[1.8, 2.8, 1.2, 2.6, 2.2, 2.2, 2.0, 2.2, 9.5],
        font=8,
    )
    if len(findings) > len(shown):
        r.para(f"Showing the first {len(shown)} of {len(findings)} findings; the JSON audit report lists all of them.", italic=True)

    overrides = [f for f in findings if f.get("override")]
    if overrides:
        r.heading("Reviewer decisions (overrides)", 2)
        r.table(
            ["Rule", "Row", "Reason", "Comment", "Decided by", "Decided at"],
            [
                [
                    f.get("check_id"),
                    f.get("affected_row_number"),
                    f["override"].get("reason"),
                    f["override"].get("comment"),
                    f["override"].get("actor_id"),
                    _date(f["override"].get("created_at")),
                ]
                for f in overrides
            ],
            widths=[2, 1.5, 7, 6, 5, 4],
        )


def _ai(r: _Report, p: dict[str, Any]) -> None:
    calls = p.get("ai_activity") or []
    if not calls:
        return
    r.heading("8. AI assistance", 1)
    r.para("AI only ever proposed; every proposal was re-checked by the deterministic rules and needed a person's approval.")
    r.table(
        ["When", "Purpose", "Outcome", "Model", "Items sent", "Items resolved", "Time (ms)", "Message"],
        [
            [
                _date(c.get("created_at")),
                _words(str(c.get("purpose") or "")),
                c.get("status"),
                c.get("model") or "None",
                c.get("items_sent"),
                c.get("items_resolved"),
                c.get("latency_ms"),
                c.get("message"),
            ]
            for c in calls
        ],
        widths=[3.6, 3, 2.4, 3.4, 1.8, 2, 1.8, 8],
        font=8,
    )


def _trail(r: _Report, p: dict[str, Any]) -> None:
    events = p.get("audit_trail") or []
    if not events:
        return
    r.heading("9. Audit trail", 1)
    r.para("Recorded events for this run, oldest first.")
    r.table(
        ["When", "Event", "Action", "Actor", "Reason"],
        [[_date(e.get("timestamp")), e.get("event_type"), e.get("action"), e.get("actor_id") or "System", e.get("reason")] for e in events],
        widths=[4, 7, 2.4, 6, 7],
        font=8,
    )


# ----------------------------------------------------------------- entry point


def build_audit_docx(payload: dict[str, Any]) -> bytes:
    """Render the stored audit payload as a Word document (tables for every section)."""
    r = _Report()
    r.doc.add_heading("Audit Report", 0)
    r.para(f"Run {_clean(payload.get('run_id'))}  |  generated {_date(payload.get('generated_at'))}", italic=True)
    _summary(r, payload)
    _run_and_source(r, payload)
    _cleaning(r, payload)
    _mapping(r, payload)
    _treaty(r, payload)
    _relations(r, payload)
    _discrepancies(r, payload)
    _ai(r, payload)
    _trail(r, payload)
    return r.bytes()
