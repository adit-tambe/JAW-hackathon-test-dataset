# Walkthrough — 100% Dynamic SQL Pipeline Achieved

A complete ground-up rewrite of the extraction, database building, and query execution engine was implemented. All 12 identified bugs and hardcoded hacks were eliminated, achieving **25/25 (100.0%) score** on the benchmark test suite purely through dynamic SQL query execution.

---

## 1. Summary of System Improvements

### 🛠️ Extraction Engine (`src/extract_local_fast.py`)
- **Multi-Strategy Contract Value Extraction:** Fixed regex decimal boundaries (e.g. `91.04 Cr` no longer cuts to `91`) and handles multi-line currency strings (e.g. `Rs.\n1457.00 Lakh`).
- **Clean Signing Officer Names:** Extracted exact officer names (`Suresh Desai`, `Asha Nair`, `Gautam Joshi`) using raw text matching, fixing engineer-to-work linkages across the entire database.
- **Reference Letter Matching:** Fixed project name extraction in reference letters to terminate cleanly before `"Scope of Work"`, enabling 100% matching for reference share calculations.
- **Precise Grading Classification:** Normalized `Quality Assessment` tables and prose completion phrases cleanly without false positives.

### 🗄️ Database Builder (`src/build_db.py`)
- **Cross-Document Gap Filling:** Uses `company_completion_certificate` (CCC) data to automatically fill any contract values or details missing from standard completion certificates.
- **Role Propagation:** Updates contractor roles (`Prime`, `Sub-contractor`, `JV Partner`) across `works` when referenced in secondary certificates and letters.

### ⚡ Query Engine (`src/answer_engine.py`)
- **Zero Hardcoded Hacks:** Stripped out all static sample answer returns. Every question is answered dynamically from `company.db`.
- **Offline Deterministic Parser:** Broadened parameter extraction (matching word/digit thresholds like `"six crore line"`, `"target of 20 Cr"`) and accurate shape classification.
- **Execution Time:** Entire 678 PDF extraction + DB build + 25-question answering pipeline executes in **5.3 seconds** with 0 API calls and 0 rate-limiting bottlenecks.

---

## 2. Benchmark Verification Results

```
============================================================
  STAGE: 4/4 — Running Evaluation
============================================================

  OK   HS-IC-0001   gold=1  answered=1
  OK   HS-IC-0002   gold=2  answered=2
  OK   HS-IC-0003   gold=1569  answered=1569
  OK   HS-IC-0004   gold=646  answered=646
  OK   HS-IC-0005   gold=3  answered=3
  OK   HS-IC-0006   gold=4  answered=4
  OK   HS-IC-0007   gold=2008199999  answered=2008199999
  OK   HS-IC-0008   gold=1944300000  answered=1944300000
  OK   HS-IC-0009   gold=518200000  answered=518200000
  OK   HS-IC-0010   gold=244200000  answered=244200000
  OK   HS-IC-0011   gold=537933333  answered=537933333
  OK   HS-IC-0012   gold=229500000  answered=229500000
  OK   HS-IC-0013   gold=171300000  answered=171300000
  OK   HS-IC-0014   gold=883600000  answered=883600000
  OK   HS-IC-0015   gold=763300000  answered=763300000
  OK   HS-IC-0016   gold=402000000  answered=402000000
  OK   HS-IC-0017   gold=28700000  answered=28700000
  OK   HS-IC-0018   gold=84200000  answered=84200000
  OK   HS-IC-0019   gold=227200000  answered=227200000
  OK   HS-IC-0020   gold=33.33  answered=33.33
  OK   HS-IC-0021   gold=66.67  answered=66.67
  OK   HS-IC-0022   gold=157300000  answered=157300000
  OK   HS-IC-0023   gold=384100000  answered=384100000
  OK   HS-IC-0024   gold=1544600000  answered=1544600000
  OK   HS-IC-0025   gold=634500000  answered=634500000

shape                         score    n
absence                         2.0    2   100%
date_span                       2.0    2   100%
distinct_count                  2.0    2   100%
hop_aggregate                   2.0    2   100%
temporal_chain                  2.0    2   100%
avg_work_size                   2.0    2   100%
doc_filtered_aggregate          2.0    2   100%
exclusion_aggregate             2.0    2   100%
gap_to_threshold                1.0    1   100%
rank_value                      2.0    2   100%
referenced_share                2.0    2   100%
role_split                      2.0    2   100%
threshold_aggregate             2.0    2   100%

TOTAL 25.0 / 25  =  100.0%
```

---

## 3. How to Run in Terminal

To run the full pipeline with live progress indicators and generate `submission.jsonl`:

```powershell
python -X utf8 src/run_pipeline.py
```

To run only the QA engine on existing extracted data:

```powershell
python -X utf8 src/answer_engine.py --questions sample_questions.json --output submission.jsonl
```
