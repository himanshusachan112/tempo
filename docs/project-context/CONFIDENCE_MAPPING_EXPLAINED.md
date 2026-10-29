# How Confidence Mapping Actually Works

A code-level explanation of how a source column in an uploaded bordereaux gets matched to a target field, how the
confidence percentage is produced, what each of the five evidence areas (Semantic, Value, Context, Layout, AI)
really measures, and where the implementation differs from the original design.

> **Scope and honesty note.** This document describes what the code does today, verified by reading the source and by
> calling the scoring functions with real inputs (the numbers in the worked examples were computed, not estimated).
> Where the running code differs from `ADR-004` or from what a bucket's name suggests, that is called out explicitly in
> [Section 9](#9-where-the-implementation-differs-from-the-design) rather than smoothed over.

**Source files covered**

| File | Role |
|---|---|
| `backend/src/bordereaux/mapping/scoring.py` | Weights, the formula, 100% rule, auto-approve flag |
| `backend/src/bordereaux/mapping/aliasing.py` | Header normalisation, exact and fuzzy matching |
| `backend/src/bordereaux/mapping/service.py` | Candidate generation, conflicts, approve/reject/manual/finalize |
| `backend/src/bordereaux/mapping/ai_assist.py` | The AI step: prompt, validation, how AI changes scores |
| `backend/src/bordereaux/mapping/target_format.py` | Default vs. custom target dictionary |
| `backend/src/bordereaux/profiling/profiler.py` | How a column's data type is inferred |
| `frontend/src/components/common/ConfidenceBadge.tsx` | How the percentage is shown |
| `docs/project-context/ADR/ADR-004-confidence-scoring.md` | The original design and its revision |

---

## 1. The big picture

Mapping answers one question per source column: **"which field of the target format is this?"** The platform follows
the project rule *"deterministic first, AI second"* (ADR-002): Python decides everything it can by rules, and the AI is
asked only about what the rules could not settle.

```
 uploaded file --> cleaning --> per-column profile (data type, null rate ...)
                                         |
        for every source column          v
   +-------------------------------------------------------------+
   | STAGE 1  exact dictionary term?   -> 100% confidence        |
   | STAGE 2  fuzzy / abbreviation >=90%-> weighted score        |
   | no match -> "extra_<header>" pass-through (confidence 0)     |
   +-------------------------------------------------------------+
                                         |
        columns still unsettled (ambiguous / unmatched)
                                         v
   +-------------------------------------------------------------+
   | STAGE 3  ONE batched AI call -> validated by Python ->       |
   |          becomes a PROPOSED suggestion, never auto-applied   |
   +-------------------------------------------------------------+
                                         |
                                         v
   reviewer: approve / reject / map manually  --> finalize mapping
```

Three things to hold on to:

1. **Confidence is a number between 0 and 1** stored per candidate with four decimals (`ExactDecimal(5,4)`), plus a
   *breakdown* of the five bucket scores and a list of *evidence* items. Both are kept so the number is always
   explainable and auditable.
2. **Nothing below an exact dictionary match is ever applied automatically.** Fuzzy, ambiguous and AI-proposed
   candidates always need a person's approval, however high their score.
3. **No column is ever dropped.** A column that matches nothing becomes an `extra_<header>` pass-through so it still
   flows through the pipeline (and numeric ones are examined later by the money-column checks).

---

## 2. The formula and the weights

`scoring.py` defines the whole formula:

```python
WEIGHTS = {"semantic": 0.30, "value": 0.25, "context": 0.20, "layout": 0.10, "ai": 0.15}

def calculate_confidence(scores):
    total = sum(scores.get(k, 0.5 if k == "ai" else 0.0) * w for k, w in WEIGHTS.items())
    return min(max(total, 0.0), 1.0)
```

| Bucket | Weight | Intended question |
|---|---:|---|
| Semantic | **30%** | Does the *name* of the column mean this field? |
| Value | **25%** | Do the *values* look like this field's data type? |
| Context | **20%** | Does the *surrounding situation* support it? |
| Layout | **10%** | Does the *position/grouping* in the sheet support it? |
| AI | **15%** | Does the *language model* agree? |
| **Total** | **100%** | |

Mechanics worth knowing:

- Each bucket score is between 0 and 1; the total is the weighted sum, **clamped to [0, 1]** and rounded to 4 decimals.
- If a bucket is **missing** from the breakdown it counts as **0**, except `ai`, which counts as **0.5** (neutral).
- The weights sum to exactly 1.0, so a total of 1.0 needs every bucket at 1.0.
- **Weights are constants in code.** ADR-004 says they "can be tuned per project", but there is no per-project override
  in the implementation; changing them means editing `WEIGHTS`.
- History: the original design had seven buckets (it also had *Relationship* 20% and *Historical* 10%, with AI at 10%).
  Iteration 1.1 removed Relationship (the arithmetic check now runs in financial checks, after treaty figures exist, because
  no column relationships are known at mapping time) and Historical (nothing is learned from past manual mappings), and
  redistributed their weight to the five that remain.

### The 100% shortcut (bypasses the formula)

If a column header is a **data-dictionary term of the field it maps to** — and it is the *only* sensible reading — the
candidate gets confidence **1.0 outright** and every bucket is recorded as 1.0:

```python
EXACT_DICTIONARY_CONFIDENCE = 1.0
def exact_dictionary_breakdown(): return {k: 1.0 for k in WEIGHTS}
```

This is deliberate: an exact term is deterministic and fully explained, so there is nothing to "weigh". The condition
(in `generate_mapping_candidates`) is:

> stage is `EXACT` **and** no other column competes for the same field **and** the header is not a term of several fields.

If any of those fails, the candidate is **not** given 100% — it falls into the weighted formula (see
[Section 5](#5-special-cases-conflicts-and-ambiguous-headers)).

---

## 3. Step 0 — normalising a header

Before any comparison, `normalize_alias()` reduces both the source header and every dictionary term to a canonical form:

1. lower-case and trim;
2. **`%` becomes the word `pct`** (so `Commission %` ≠ `Commission`; otherwise both would collapse to "commission"
   and collide with two genuinely different fields);
3. `_`, `-`, `/` become spaces;
4. every other punctuation character becomes a space;
5. runs of spaces collapse to one.

Examples (computed): `GROSS_PREMIUM (100%)` → `gross premium 100 pct`; `Policy No.` → `policy no`;
`Coverholder Comm Amt` → `coverholder comm amt`.

Each target field owns a set of normalised terms: its **name**, its **display name**, and all its **aliases**
from the dictionary (`bordereaux_pc_v1.yaml`, 58 fields in the default dictionary). In a **custom** target format the
user's own column names and the "recognised terms" they type in are added as aliases, with the same rules.

---

## 4. The two stages that settle most columns

### Stage 1 — exact dictionary term

The normalised header is looked up in every field's term set.

| Outcome | Result |
|---|---|
| Exactly one field contains it, **or** several do but exactly one has it as its *own name / display name* ("primary") | That field wins at stage `EXACT`. Confidence **100%** (unless a conflict, below). |
| Several fields contain it and none (or more than one) is primary (e.g. `Line`, `Category`) | **Ambiguous alias**: see Section 5. Never a silent 100%. |
| Nobody contains it | Go to Stage 2. |

Real results from the default dictionary: `Gross Premium` → `gross_premium` (exact); `Coverholder Comm Amt` →
`commission` (exact, via alias); `Policy No.` → `policy_ref` (exact, via alias); `Line` → `line_id` *and*
`class_of_business` (ambiguous); `Category` → `section` *and* `class_of_business` (ambiguous).

### Stage 2 — fuzzy match (threshold 0.90)

Used only when Stage 1 found nothing. There are two measures, tried in order, and **nothing below 0.90 is ever
proposed** ("never guesses below it"; `FUZZY_THRESHOLD = 0.90`, mirrored in the YAML `resolution.fuzzy_threshold`).

**(a) Whole-string similarity** — RapidFuzz `fuzz.ratio(header, term)/100`, taken as the best score over *every term of
every field*. Catches typos: `Nett Prem` → `net_premium` at **0.9412**; `Gross Prm` → `gross_premium` at **0.9474**.

**(b) Token-wise abbreviation match** — used when (a) falls short. It handles abbreviations that are poor string
matches but obvious to a human (`Grs Prem` → `gross_premium` at **0.955**, `Pol Nmbr` → `policy_ref` at **0.9583**).
Rules:

- the header and the term must have the **same number of words**;
- each word pair scores 1.0 if equal; otherwise, if the source word is an **abbreviation** of the term word (same
  first letter, letters appear in order, strictly shorter, at least 40% of the word's length) it scores
  `0.85 + 0.10 × (len(short)/len(full))` — the more of the word is kept, the better; otherwise plain `fuzz.ratio`;
- **every** word pair must reach at least **0.85**, and the **mean** must reach **0.90**;
- if the best two fields are within **0.02** of each other, it returns *nothing* — "several fields fit equally well:
  leave it to the reviewer".

A header that clears neither stage (e.g. `Amount 3`) is **not mapped**: it becomes `extra_amount_3` with confidence 0
and a reason of `numeric_no_dictionary_match` (profiled numeric) or `no_canonical_match` (anything else).

### What score does a Stage 2 hit get?

For fuzzy candidates, `service.py` builds the breakdown like this:

```python
breakdown = {"semantic": score, "value": value_score, "context": score, "layout": 0.5, "ai": 0.5}
```

where `score` is the fuzzy similarity (0.90–<1.0) and `value_score` comes from the type check in Section 6.2.
Look closely: **`context` is just a copy of `semantic`, `layout` is a constant 0.5, and `ai` is the neutral 0.5.** This
is the single most important thing to understand about the numbers, and is dissected bucket-by-bucket below.

---

## 5. Special cases: conflicts and ambiguous headers

### 5.1 Ambiguous alias (header is a term of several fields)
- The candidates are ranked by **value-type fit**; the best fit leads, the others are created as *alternatives*.
- The lead candidate gets semantic = context = **0.8**, the alternatives too (flat 0.8 — the evidence is "a recognised
  term, but of several fields").
- All are flagged `is_ambiguous` and carry `ambiguous_alias` evidence listing the competing fields. They are **never**
  auto-approve eligible and are **never** counted by "approve all exact".

### 5.2 Duplicate-target conflict (two columns want the same field)
- Winner is chosen by (stage rank: EXACT > FUZZY) → (higher score) → (**earlier column in the sheet**).
- Every column in the conflict — including the winner — is scored with the **weighted formula** (not the 100%
  shortcut), and the loser(s) are marked ambiguous. Evidence `duplicate_target_conflict` records who won.
- Consequence: an exact term can score **0.875** instead of 1.0 when a second column competes for the same field.

### 5.3 Pass-through, manual and rejected
| Situation | Confidence | Breakdown | Origin |
|---|---:|---|---|
| No match at all → `extra_<slug>` | 0 | empty | `AUTO_APPROVED` |
| Reviewer maps manually | 1.0 | empty (evidence `manual_override`) | `MANUAL` |
| Reviewer rejects the only candidate | falls back to `extra_*` | empty | `AUTO_APPROVED` |

The UI shows **n/a** for `extra_*` rows instead of a percentage.

---

## 6. The five buckets, dissected

For each bucket: what it is **meant** to measure, what the code **actually** computes, the exact values it can take, and
how much it can move the total.

### 6.1 Semantic — weight 30%

**Meant to measure:** does the column's *name* mean this field?

**What the code computes:** the **header-to-dictionary-term similarity** produced in Stage 1/2:

| Situation | Semantic score |
|---|---|
| Exact, unambiguous dictionary term | 1.0 (via the 100% shortcut) |
| Fuzzy whole-string match | `fuzz.ratio/100` (0.90–<1.0) |
| Fuzzy abbreviation match | mean token score (0.90–<1.0; abbreviation words score 0.85–0.95) |
| Ambiguous alias (per candidate) | flat 0.8 |
| Duplicate-target conflict | the match score (1.0 for an exact term, still weighted) |
| AI-only suggestion with no name evidence | flat 0.5 (neutral) |

**What it is not:** it is not semantic in the "embedding / meaning" sense. It is lexical (string and abbreviation)
similarity against a curated list of terms. "Premium Written" will not match `gross_premium` unless that phrase is a
listed alias. Understanding of unlisted synonyms is delegated to the AI step.

**Influence:** it is the strongest single driver; combined with Context (below) the name carries **50%** of the
weight in practice.

### 6.2 Value — weight 25%

**Meant to measure:** do the column's *values* look like the target field's data?

**What the code computes:** a **type-compatibility table** between the column's *profiled* type and the field's
declared type. The column type comes from the profiler: after dropping blanks, if ≥ **70%** of values parse as numbers
(and numbers ≥ dates) it is `INTEGER` (all whole numbers) or `DECIMAL`; else if ≥ 70% parse as dates, `DATE`; else
`STRING`; nothing to look at → `UNKNOWN`.

| Column type vs. field type | Value score |
|---|---:|
| Both numeric (`INTEGER`/`DECIMAL` in either combination) | **1.0** |
| Identical types (including `DATE`/`DATE`) | **1.0** |
| Column `STRING`, field `STRING`/`ENUM`/`COUNTRY_CODE`/`CURRENCY_CODE` | **0.8** |
| Column `UNKNOWN` (empty/unprofilable) | **0.5** |
| Anything else (e.g. numeric column → `DATE` field) | **0.3** |

**What it is not:** it only looks at the **type**. It does not check value ranges (is a "percentage" between 0 and
1?), formats (do dates look like dates in the right locale?), patterns (does it look like a currency code?), distinct
counts, or uniqueness. A `STRING`-typed column that holds free text and one holding ISO currency codes both score 0.8
against `currency_code`.

**Influence:** the only bucket that can *discriminate between two candidates for the same header* in deterministic
mode — it is what ranks the alternatives for an ambiguous header like `Line`.

### 6.3 Context — weight 20%

**Meant to measure:** does the surrounding situation (neighbouring columns, header patterns) support the mapping?

**What the code computes:** **the same number as Semantic.** In every place the deterministic breakdown is built,
`"context": score` (or `0.8`, or `0.5`) is set equal to the semantic value. There is no neighbour analysis, header
pattern analysis or cross-column reasoning in the scoring code.

**Where context *is* actually used:** the *AI prompt* receives each unresolved column's `previous_column`,
`next_column` and `position` (Section 7). So neighbours influence the **AI's** answer, but not the deterministic Context
bucket.

**Influence:** because it duplicates Semantic, it effectively raises the weight of the *name* from 30% to **50%**.

### 6.4 Layout — weight 10%

**Meant to measure:** does the sheet's structure (column groupings/patterns) support the mapping?

**What the code computes:** a **constant 0.5** for every candidate (deterministic and AI-created). Nothing
reads the sheet layout. (The 100%-shortcut candidates record 1.0 only because *all* buckets are recorded as 1.0 for
display.)

**Influence:** a fixed **+0.05** on every weighted total (0.5 × 10%). It cannot separate good candidates from bad ones.

### 6.5 AI — weight 15%

**Meant to measure:** does the language model agree?

**What the code computes:** neutral **0.5** until the AI has answered about that column; afterwards, the model's
self-reported confidence (details in Section 7).

| State of the AI bucket | Value |
|---|---|
| AI off / not configured / not needed / failed / column not sent | 0.5 (neutral) |
| AI proposed *this* field for this column | the model's confidence (0–1; missing/invalid → 0.5) |
| AI proposed a *different* field for this column | this candidate's AI bucket is set to **0.2** |

**Influence on a total that already exists:** `0.15 × (ai − 0.5)`, i.e. between **−0.075** (ai = 0) and
**+0.075** (ai = 1). The bucket is deliberately small enough that **the model can nudge a candidate but cannot
rescue a poor one or overturn a strong deterministic match.**

### 6.6 What the five buckets add up to in practice

Because Context = Semantic and Layout/AI are constants in deterministic mode, the weighted formula for a non-exact
deterministic candidate reduces to:

```
total = 0.30·S + 0.25·V + 0.20·S + 0.10·0.5 + 0.15·0.5
      = 0.50·S + 0.25·V + 0.125
```

with `S` = name similarity and `V` = the value-type score. That is why the totals below look the way they do.

---

## 7. Stage 3 — the AI step in detail

### 7.1 When it runs (and when it deliberately doesn't)
It runs once per mapping generation (and on "Retry AI") for columns that are **both** unsettled **and** not yet answered:

- columns carrying `ambiguous_alias` or `duplicate_target_conflict` evidence, and
- `extra_*` pass-throughs whose reason is `no_canonical_match` or `numeric_no_dictionary_match`.

It is **skipped** (with a visible reason recorded) when: the user turned AI off for the run; AI isn't configured; no
column needs it ("the deterministic mapping settled everything"); or the column was already decided by a person or by a
100% match. At most **60 columns** go in one call (the rest are reported as "not sent").

### 7.2 What the model is given
One JSON document containing, per unresolved column: its header and position, previous and next headers, a profile
summary (type, filled/total rows, distinct count, numeric min/max/mean), up to **8 distinct sample values** (each ≤ 60
chars, from the first 200 rows), why it was unresolved, and what the deterministic stage proposed. It also gets the
target-field catalogue (name, display name, type, money role, mandatory flag, up to 6 aliases), the list of fields
already settled, and explicit rules: choose only listed names, never an already-mapped field, one column per field,
percentages are not money amounts, **answer null rather than guess**, give a confidence and a ≤ 25-word reason.
Temperature is 0.0.

### 7.3 Validation — Python re-checks every answer
A decision survives only if it names a **real** target field, that field is **not already taken**, the column's
profiled type is **compatible** (value-type fit ≥ 0.5), and if two columns want the same field the **more confident**
one keeps it. Anything else is discarded with a note. A `null` answer is kept as an "AI found no match" note on the
pass-through row. The model's confidence is clamped to 0–1 (missing/non-numeric → 0.5).

### 7.4 How a validated answer changes the scores
| Case | What happens |
|---|---|
| AI picks a field that already has a PROPOSED candidate for this column | The `ai` bucket is **replaced** by the model's confidence; the total is recalculated; an `ai_suggestion` evidence item (with reason, model, provider) is appended. |
| AI picks a field with no existing candidate | A **new PROPOSED candidate** is created with `{semantic 0.5, value <type fit>, context 0.5, layout 0.5, ai <model confidence>}`; not auto-approve eligible. |
| AI says null | `ai_note` evidence on the pass-through row; no score change. |
| Other candidates for the same column that the AI did *not* choose | Their `ai` bucket is set to **0.2** (a −0.045 shift from neutral) and `ai_preferred_other` evidence is added. |

**Hard ceiling for AI-only suggestions: 70%.** With semantic/context/layout all neutral, the best possible total is
`0.5·0.30 + 0.5·0.20 + 0.5·0.10 + 1.0·0.25 + 1.0·0.15 = 0.70`. An AI-proposed field with no name evidence can therefore
**never** look like a high-confidence match, by construction.

### 7.5 Safety properties
The AI result is only ever a PROPOSED candidate; a failed, timed-out, rate-limited or disabled call changes nothing and
is recorded in `ai_interactions` so the UI can show what happened; nothing the AI says is applied without a reviewer.

---

## 8. From score to decision

### 8.1 Approval rules
- `auto_approve_eligible` is true only when stage is **EXACT**, **not ambiguous**, and total **≥ 0.80** — in practice
  exactly the 100% dictionary matches. Fuzzy, ambiguous and AI candidates are never eligible.
- The **"Approve all exact matches"** action approves every still-open candidate with confidence **≥ 1.0**.
- Everything else is approved, rejected, or overridden manually by a person. Approving one candidate marks its sibling
  candidates for that column `SUPERSEDED`; a target field can only be mapped from one column.
- **Finalize** requires every *mandatory* field that appears among the candidates to be approved. Mandatory fields that
  are absent from the file altogether are treated as "source gaps" and don't block. Any column still undecided is
  carried through as `extra_*`.

### 8.2 How it is shown
`ConfidenceBadge` shows `NN% confidence` with a colour — **green ≥ 80%, amber ≥ 50%, red below** — and expands to a
table of the five bucket percentages. Pass-through rows show "n/a". Evidence is rendered as sentences ("AI suggests this
field (78% sure): …", "Numeric column with no dictionary match; checked against premium relationships after treaty
figures", "Assigned manually by a reviewer").

---

## 9. Worked examples (numbers computed from the real functions)

All use `total = 0.50·S + 0.25·V + 0.125` unless stated.

| # | Header → field | S | V | Total | Why |
|---|---|---:|---:|---:|---|
| 1 | `Gross Premium` → `gross_premium` | exact | – | **100%** | Exact dictionary term; shortcut. |
| 2 | `Coverholder Comm Amt` → `commission` | exact | – | **100%** | Alias of `commission`. |
| 3 | `Grs Prem` (numeric) → `gross_premium` | 0.955 | 1.0 | **85.3%** | Abbreviation match; numeric/numeric. |
| 4 | `Nett Prem` (numeric) → `net_premium` | 0.9412 | 1.0 | **84.6%** | Typo match. |
| 5 | `Gross Prm` where the data profiled as text | 0.9474 | 0.3 | **67.3%** | Good name, but string data for a numeric field: Value drags it down. |
| 6 | Fuzzy at the minimum (S 0.90) for a text field | 0.90 | 0.8 | **77.5%** | The weakest allowed name match with a decent type fit. |
| 7 | Fuzzy at the minimum, numeric into text-typed field | 0.90 | 0.3 | **65.0%** | **Lowest possible fuzzy total.** |
| 8 | `Line` (numeric) → `line_id`/`class_of_business`, lead | 0.8 | 1.0 | **77.5%** | Ambiguous alias; flat 0.8 name score. |
| 9 | Same, text-typed lead | 0.8 | 0.8 | **72.5%** | |
| 10 | Exact term but a 2nd column also maps to that field | 1.0 | 1.0 | **87.5%** | Conflict removes the 100% shortcut. |
| 11 | `Amount 7` → AI says `commission`, conf 0.90, numeric | 0.5 | 1.0 | **68.5%** | AI-only: `0.15+0.10+0.05+0.25+0.135`. |
| 12 | Same, AI conf 0.50 | 0.5 | 1.0 | **62.5%** | |
| 13 | Ambiguous alternative the AI did *not* pick (ai → 0.2) | 0.8 | 1.0 | **73.0%** | 77.5% − 4.5 points. |
| 14 | Reviewer maps manually | – | – | **100%** | Human decision, `MANUAL`. |
| 15 | Unmatched column | – | – | **0 (n/a)** | `extra_*` pass-through. |

**Reading the ranges**

| Candidate kind | Lowest possible | Highest possible |
|---|---:|---:|
| Exact dictionary term (no conflict) | 100% | 100% |
| Exact term in conflict | 87.5%* | 87.5%* |
| Fuzzy / abbreviation | **65.0%** | just under **87.5%** |
| Ambiguous alias | 60.0% | 77.5% |
| AI-only suggestion | 42.5% (value fit 0.5, AI confidence 0) | **70.0%** |

\* with a numeric/numeric or identical-type fit; lower if Value is lower.

Therefore, with the shipped weights, **only an exact dictionary match can exceed ~87.5%**, which fits the policy that
only dictionary matches are trusted without a human.

---

## 10. Where the implementation differs from the design

ADR-004 describes a richer system than the code. These are the places a reader would be misled by the names alone.

| # | Observation | Design (ADR-004) | Implementation | Practical effect |
|---|---|---|---|---|
| 1 | **Layout is a placeholder** | "Column groupings" | Constant `0.5` | Adds a flat +5% to every weighted score; no signal. |
| 2 | **Context duplicates Semantic** | Position, adjacent columns, header patterns | `context = semantic` | Name effectively counts for 50%, not 30%. Neighbour headers only reach the AI prompt. |
| 3 | **Value is type-only** | Type (0.5) + numeric range (0.3) + pattern (0.2) | Table of type pairs | No range/pattern/format checks. |
| 4 | **Semantic is lexical** | Jaro-Winkler on the field *name* | RapidFuzz ratio on *all terms* + abbreviation rule | Works well on typos/abbreviations; synonyms rely on the alias list or AI. |
| 5 | **Auto-approve threshold** | ≥ 0.95, no rivals > 0.7, user opt-in | EXACT + unambiguous + ≥ 0.80 (which equals 100% in practice); "approve all exact" is an explicit user click | Nothing is approved silently. |
| 6 | **Weights not tunable** | Per-project overrides | Constants in `scoring.py` | Change requires a code edit. |
| 7 | **Two value-type tables** | – | `service._value_type_score` (INTEGER/DECIMAL only) and `scoring.value_type_score` (also treats `PERCENTAGE` as numeric; used by the AI step) | A `DECIMAL` column vs a `PERCENTAGE` field scores 0.3 in the deterministic path but 1.0 in the AI path. Worth unifying. |
| 8 | **Inconsistent colour/threshold bands** | – | UI badge 80/50; fuzzy floor 90; approve-exact 100; auto flag 80; the Word audit report uses 90/70 | Different screens may label the same number differently. |
| 9 | **Coverage fields never written** | `mapping_sets.coverage_required/optional` | Columns exist but nothing sets them; coverage is computed in the UI | The audit report's "Required-field coverage" currently reads *Not available*. |
| 10 | **Schema-template boosting** | `CLAUDE.md` describes saved templates boosting confidence | `format_templates.py` manages saved *target formats* only and contains no confidence logic | No template boost exists in scoring today. |

None of these is a bug in the sense of wrong arithmetic — the formula is applied consistently — but items 1–3 mean two of
the five headline buckets carry no independent information, so the percentage is best read as
**"how good is the name match, adjusted for data type, capped by a neutral AI/layout floor."**

---

## 11. Frequently asked questions

**Why does a perfect-looking fuzzy match top out around 85%, not 100%?**
Layout (0.5) and AI (0.5) are neutral, contributing only 0.125 of the possible 0.25 — and Semantic/Context can't reach 1.0
because an exact term would have taken the 100% path. This is intentional pressure toward human review.

**Why can the AI never produce a high score by itself?**
An AI-only suggestion has no name evidence (Semantic/Context 0.5), so its ceiling is 70% (Section 7.4). The AI can raise
an existing candidate by at most 7.5 points.

**Why is an exact header sometimes below 100%?**
Either the header is a term of several fields (ambiguous alias → 0.8 name score), or another column also maps to the
same field (conflict → weighted score). Both are shown with `ambiguous` evidence.

**Does the AI see my data?**
It sees headers, neighbouring headers, profile statistics and up to 8 distinct sample values per unresolved column from
the first 200 rows — not the full file. It is skipped entirely if AI is off.

**Where is each decision recorded?**
Candidates keep their `confidence_total`, `confidence_breakdown` and `evidence` permanently; approvals, rejections,
manual overrides and finalization are written to the audit log; each AI call is recorded in `ai_interactions`. The Word
audit report prints the breakdown and evidence for every approved mapping.

---

## 12. Quick reference

```
Weights        semantic .30 | value .25 | context .20 | layout .10 | ai .15
Exact term     100%  (all buckets 1.0)        -> only path to >87.5%
Fuzzy gate     >= 0.90 similarity; abbreviation words >= 0.85 each, tie margin 0.02
Deterministic  total = 0.50*S + 0.25*V + 0.125          (S: name score, V: type fit)
Value scores   numeric/numeric 1.0 | same type 1.0 | string->text-ish 0.8 | unknown 0.5 | other 0.3
Ambiguous      name score 0.8, never auto-approved
AI             neutral 0.5; chosen field = model conf; non-chosen alternatives = 0.2
AI-only max    70%        AI nudge on existing candidate: +/- 7.5 points max
Auto-approve   EXACT + unambiguous + >= 0.80 (= the 100% matches)
UI colours     green >= 80% | amber >= 50% | red < 50%
```
