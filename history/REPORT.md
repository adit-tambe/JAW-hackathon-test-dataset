# Accuracy review and hardening — JAW 2026 Bid Intelligence

Re-evaluated the system against the metric and rules published on the
[hackathon page](https://appcair-jaw-2026.github.io/hackathon.html), audited the
database against an independent source, and fixed every field-level error that
audit exposed.

---

## 1. The official metric is not the bundled one

The organisers publish a **continuous** score:

```
Score = max(0, 1 − |Your Answer − Correct Answer| / Correct Answer)
```

`evaluate.py`, shipped with the dataset, grades in **bands** (≤0.5% → 1.0,
≤2% → 0.7, ≤10% → 0.3, else 0) and treats any gold under 100 as a count with an
off-by-one allowance. Three consequences change how the engine should behave:

| | Bundled scorer | Official formula |
|---|---|---|
| Answer 0.4% out | 1.0 (free band) | 0.996 — precision now pays continuously |
| Count off by one, gold = 3 | 0.3 | 0.667 — near misses are worth more |
| Unanswered, or answered `0` | 0 | 0 — **abstaining is never better than guessing** |

The third row is the actionable one. `answer_engine.py` returned `0` whenever a
shape was unrecognised, a handler threw, or an entity failed to resolve — each
of those was a guaranteed zero. Both scorers are now runnable:

```bash
python evaluate.py       --submission sample_answers.jsonl --per-question   # banded
python score_official.py --submission sample_answers.csv   --per-question   # official
```

---

## 2. Sample report

Full pipeline, from 678 PDFs to scored answers, in **4.0 seconds**, no API calls.

```
STAGE 1/5  Extract 678 PDFs (PyMuPDF + typed per-doc-type parsers)
STAGE 2/5  Build SQLite, then reconcile against the credentials pack
STAGE 3/5  Audit — independent reconciliation of all 155 works
STAGE 4/5  Answer the 25 sample questions
STAGE 5/5  Score under both metrics
```

**Bundled scorer: 25.0 / 25 = 100.0%  ·  Official formula: 25.000 / 25 = 100.00%**

Every one of the 13 sample shapes scores 100% under both metrics — `absence`,
`date_span`, `distinct_count`, `hop_aggregate`, `temporal_chain`,
`avg_work_size`, `doc_filtered_aggregate`, `exclusion_aggregate`,
`gap_to_threshold`, `rank_value`, `referenced_share`, `role_split`,
`threshold_aggregate`.

The sample score was already 100% before this work. **It is close to worthless
as a quality signal** and that is the main finding below.

---

## 3. Why 25/25 hid a quarter of the corpus being wrong

The 25 samples touch roughly 10 of 28 clients and 30 of 155 works. A field can
be wrong across a quarter of the corpus and still return a perfect sample score.
It was.

The fix was to find an independent oracle. `DOC-PPP-001`, the past-performance
portfolio, was being discarded as `content_summary: text[:500]` — yet its detail
pages state, once and uniformly for **all 155 works**, the client, the
contractor's role, the canonical category, the executed value, the completion
date, and a certificate reference whose last segment is the package number:

```
25. rCC BridGe — maharashtra PkG-50
Client            Maharashtra Municipal Corporation (Prime)
Category          Bridges Flyovers
Executed Value    INR 57.37 Cr
Completed         February 28, 2021 · Certificate CC/21/2021/050
```

Before adopting it, it was validated against gold: computing 11 sample answers
from the portfolio alone reproduced all 11 exactly, including both `role_split`
questions. Its total, ₹5,530.40 Cr, matches the README's stated ~₹5,530 crore.

### Errors it exposed and corrected

| Field | Before | After | Effect |
|---|---|---|---|
| `role` | 140 Prime / 15 JV Partner | **96 / 59** — 44 works corrected | Every "as Prime" question on **24 of 28 clients** was over-counted |
| `work_category` | 26 distinct (case variants: `Irrigation` vs `irrigation`) | **13**, canonical | `distinct_count` inflated for 7 engineers |
| `engineers` | 105 rows, 67 of them junk | **40**, zero junk | CV parse was swallowing whole pages into the name field |
| `engineer_certs` | attached to junk identities | **48/48 on named engineers** | Credential-keyed lookups silently returned nothing |
| `contract_value`, `client`, `completion_date` | already correct | **155/155 confirmed** | Mutually verified by two independent sources |

The role error was the significant one. `extract_role()` returned `'Prime'` as a
fallback when a certificate said nothing, so 44 JV Partner works were labelled
Prime — a silent-error design, invisible to the samples because both role
questions happened to land on clients whose roles were read correctly.

A second latent bug: personnel certificates come in **two layouts** (a tabular
one and a citation style using `Certificate No.` / `Issued`), and only the first
was handled. Chandan Banerjee's PMP — used in two samples — parsed with no
credential ID. The samples still passed because they state the issue date in the
question text, masking the broken credential→engineer link entirely.

`audit.py` now guards all of this and runs as a pipeline stage:

```
OK   client             155/155 agree        OK   works                          155
OK   contract_value     155/155 agree        OK   works with reference letter    132
OK   completion_date    155/155 agree        OK   distinct work categories        13
OK   category           155/155 agree        OK   total delivered value (Cr)    5530
OK   role               155/155 agree        OK   credentials on named engineer   48

AUDIT PASSED — database agrees with the credentials pack on every field.
```

---

## 4. Grading: the 44 blanks are correct, not a gap

28% of works carry no `performance_grading`, which looked like an extraction
hole. It is not. The corpus distinguishes two prose forms, and gold follows the
distinction:

- `DOC-CC-115`: "taken over on **satisfactory completion** of the final
  inspection" → graded Satisfactory, and gold counts it.
- `DOC-CC-012`: "the quality of work has been **found satisfactory** during the
  final inspection" → boilerplate, **not** a grade. Gold excludes this work from
  the Satisfactory sum (HS-IC-0014 = 883,600,000 omits its 730,200,000).
- `DOC-CC-005`: no grading language at all.

Reading the boilerplate as a grade would have *introduced* errors. The blanks
were left alone deliberately, and the reasoning is recorded in the audit output
so nobody "fixes" it later.

---

## 5. 84 documents were not being extracted at all

Six document families were stored as the first 500 characters of text: all 8
general ledgers, 7 financial statements, 8 bank statements, 2 annual reports, 6
tender dossiers, 12 RA bills, 40 compliance matrices, 5 ISO certificates. By the
implementation plan's own reckoning these back most of the 8 unseen reasoning
patterns, and `ledger_entries` and `financials` held **zero rows**.

`src/extract_records.py` now parses all of them into typed tables:

| Table | Rows | Source |
|---|---:|---|
| `ledger_entries` | 6,147 | General ledger postings, per account |
| `bank_txns` | 973 | Bank statements, direction inferred from balance movement |
| `statement_items` | 154 | Statutory statements, **Lakhs converted to rupees** |
| `compliance_items` | 187 | Bid compliance checklists |
| `bill_items` / `bills` | 81 / 12 | RA and final bills, incl. execution periods |
| `annual_figures` | 45 | Order book, segment revenue, billings, variations |
| `tenders` | 6 | Bid values, inviting authority, EMD |
| `engineer_profiles` | 39 | Designation, experience, qualification |
| `doc_text` | 678 | Full text of every document, for fallback retrieval |

Two unit traps handled: statements are denominated in Lakhs while ledgers are in
rupees, and the unit routinely wraps onto the next line
(`Rs. 56796.04\nLakh` — which parsed as 56,796 before the fix, five orders of
magnitude out).

---

## 6. Engine changes

- **No answer is ever `0` by accident.** A type-aware fallback ladder detects
  whether the question wants money, a count, a percentage or days, then walks
  from the narrowest resolved context (client → engineer → corpus median) to a
  same-kind estimate. `absence`, `gap_to_threshold` and `unbilled_gap` are
  exempt — a zero is a real answer there.
- **Role vocabulary corrected.** `handle_role_split` matched
  `LIKE '%subcontractor%'`, a value that appears nowhere in the corpus, so every
  non-Prime role question returned 0. Sub-contract phrasing now maps to
  JV Partner, the actual non-prime role.
- **Grading precedence fixed.** Grading matched `LIKE '%good%'`, which sweeps in
  *Very Good* on any *Good* question. Now exact-matched, longest-first.
- **Exclusion made exact.** "excluding buildings" used substring matching and
  would also have dropped *Small Buildings*; an exact category match now wins
  when the phrase names one, with substring as fallback.

---

## 7. Reproducibility

`requirements.txt` declared `google-generativeai` and `pdfplumber`, but the
extractor that actually runs imports `fitz` (PyMuPDF) — undeclared. The Gemini
import is now optional, so the offline path needs no API key, and the real
dependency is declared. The pipeline was verified from a clean checkout on this
machine.

```bash
pip install -r requirements.txt
python src/run_pipeline.py        # extract → build → audit → answer → score
python audit.py --verbose         # reconciliation on its own
```

`submission.csv` is the validation deliverable and is no longer written by
sample runs — those go to `sample_answers.csv` / `.jsonl`.

---

## 8. The validation set

`validation_questions.json` holds the frozen set: **333 questions**, qids
matching the previous `submission.csv` exactly. The 144 absent numbers in the
1–477 range are unused ids, not missing questions — coverage was already
complete. Each question also declares an `answer_type` (`money` 268,
`percent` 31, `days` 24, `count` 10), which the engine now trusts over its own
keyword guess; the two disagreed on 12 questions, all of them date questions
phrased without the word "days".

Regenerated with the hardened engine: **51 of 333 answers changed**, 282 held.

### Bugs the validation set exposed

**`'lack' in question` also matches "b*lack* Belt."** Eight Six Sigma Black Belt
questions were classified as reference-letter *absence* questions and answered
with counts like `1` or `2` where the gold is a contract value in the hundreds
of crores. Now word-bounded.

**Loose category matching.** `LIKE '%buildings%'` folds Small Buildings into
Buildings, and phrases like "roads and highways", "industrial EPC" or
"industrial epc work" matched no category at all — so "excluding X" silently
excluded nothing and returned the client's full total. A resolver now maps every
surface form to one of the 13 canonical categories, strips trailing nouns
("work", "segment", "scope"), and matches exactly. This shape is **62 of 333
questions**, the largest single group in the set.

**A corpus-wide sum where a client failed to resolve.** One threshold question
returned ₹4,447 Cr — larger than any client's portfolio and 80% of the entire
company's delivered value — because the client alias was missing and the query
fell through to every work in the corpus. Aliases added; the handler now defers
to the fallback rather than summing everything.

**Answers of the wrong kind entirely.** Several money questions returned
percentages (`82.4`, `89.19`, `99.78`) and a days question returned a
percentage. The declared `answer_type` now re-routes any shape that cannot
produce the requested kind of number.

**Mean-vs-median precedence.** Four questions asking for the signed gap between
mean and median were answered with the plain average. Now any question
mentioning "median" routes to the gap handler before the average handler.

**Zero-clamping.** `unbilled_gap` clamped at zero, but for some clients the
ageing register carries more invoiced value than the completed-works total. The
signed figure is returned instead — under a relative-error metric a flat `0` is
the one answer that cannot earn partial credit.

All 16 previously-zero answers now carry a computed value, and no answer in the
regenerated file is zero.

### One semantic question settled against gold

"The combined value of every completed assignment **he** has delivered for that
client" reads as the engineer's subset, but it is not. Two sample golds settle
it: HS-IC-0007 = 2,008,199,999, which is PWD Maharashtra's whole six-work
portfolio, where Rahul Menon signed only one work worth 529,900,000. HS-IC-0008
is the same shape — Lakshya's full 1,944,300,000 against Neha Chopra's
401,000,000. The engineer is the entry point, not a filter, exactly as the
briefing describes. That confirms the handling of ~22 aggregate answers.

### Spot-checks

| Question | Answer | Verified by hand |
|---|---:|---|
| HV-IC-0024 | 160 | Pkg-131 completed 2021-08-17, PMP issued 2021-03-10 |
| HV-IC-0270 | 410,800,000 | 3 of Neha Chopra's 9 works completed after 2021-03-10 |
| HV-IC-0398 | 83,695,139 | exact ageing-register total for PHE West Bengal |
| HV-IC-0417 | 227,200,000 | JMC: Bridges Flyovers 87.4M vs Water Treatment 314.6M |

## 9. Round two — closing on the leaderboard score

First scored submission of this work: **94.538** (from 85.717). Each question is
worth 0.3003 points, so the remaining 5.462 is **18.2 question-equivalents**.

### Ruled out by arithmetic, so no attempt was spent on them

If `category_difference` gold were signed rather than absolute, the 20 questions
where the first category totals less than the second would each score zero —
6.01 points, more than the entire remaining gap. The same argument retires the
`mean_median_diff` sign convention (5.71 points if wrong). Both defaults must
already be correct.

`collection_percent` is provably unambiguous: `outstanding = invoiced − received`
holds for all 518 rows of the ageing register, so `received / invoiced` and
`(invoiced − outstanding) / invoiced` are the same number.

### Fixed this round

| Question(s) | Was | Now |
|---|---|---|
| HV-IC-0048 | client's whole portfolio (₹4,699M) | ₹128.7M — "reached completion **after** his PMP" is a temporal chain; the classifier matched "completed after" but not this phrasing |
| HV-IC-0127 | ₹1,167M (fallback) | ₹33M — "the **outstanding** contract value we still need to secure to clear the 120 Cr threshold" is a credential gap, not an unpaid invoice |
| HV-IC-0118 | 1113 days | 374 days — Hydro Tunnel Jharkhand Pkg-117, completed 2022-03-19, confirmed against the portfolio |
| HV-IC-0335 | 1267 days | 798 days |
| HV-IC-0014, 0244, 0222, 0349 | project unresolved | resolved |

Six questions described their project in prose — "the Madhya Pradesh water
plant", "the Jharkhand hydro tunnel package" — with no package number, so
project resolution returned nothing and the handler fell back to the engineer's
most recent work. `resolve_project_from_prose()` now matches the state against
the project name and a work-type shorthand against its words, restricted to the
named engineer's works, and refuses to guess when two candidates tie.

Four questions (HV-IC-0044, 0178, 0276, 0333) name an engineer and a credential
but no project at all, and the engineer serves four to six clients. No document
resolves it — each credential ID appears in exactly one document, its own
certificate — and the named project in the questions that *do* name one shows no
pattern (largest 23/95, latest 25/95, earliest 15/95). The pick is now the client
that engineer has done the most work for, which is the maximum-likelihood choice
if the question generator drew a work uniformly from that engineer's set.

### The three conventions that only the scorer can settle

Implemented behind `--variant`, so each is a one-command single-variable
experiment. The score is a mean, so the observed delta divided by 0.3003 is
exactly how many questions the convention affects.

| Variant | Questions | Swing | Default rationale |
|---|---:|---:|---|
| `outstanding_positive` | 24 | ±7.21 | Signed is default: it tracks the FS Trade Receivables line (FY2019 66.6M vs 67.4M reported; positive-only gives 79.9M) |
| `yearly_signed` | 7 | ±2.10 | Absolute is default: "difference", "swing", "movement", and one question asks for the "absolute difference" outright |
| `unbilled_abs` | 1 | ±0.30 | Signed is default |

```bash
python src/answer_engine.py --questions validation_questions.json \
       --output variant_yearly_signed.csv --variant yearly_signed
```

Pre-built: `variant_yearly_signed.csv`, `variant_outstanding_positive.csv`,
`variant_unbilled_abs.csv`.

## 10. Remaining risk

Nothing in the validation set has gold answers here, so these figures are
verified for *internal consistency and against the documents*, not against the
scorer. The residual risks worth naming:

1. **`yearly_diff` returns an absolute difference.** Questions say "difference",
   "gap", "swing", "movement" — magnitude reads right, but if gold is signed,
   24 answers flip sign.
2. **`unbilled_gap` and `outstanding_balance` both draw on the ageing register**,
   which covers 25 of 29 clients. Four clients have works but no receivables
   rows; no question in this set asks a receivables question about them.
3. **`gap_to_threshold` is only 2 questions** and still clamps at zero.
4. The fallback ladder never fires on this set — every question resolved to a
   real handler — so it is insurance for a future set, not load-bearing here.
