# BITS Hackathon — Bid Intelligence System: Implementation Plan

Build an automated pipeline that ingests 687 unstructured documents (678 PDFs + 9 Excel workbooks) from a synthetic construction company, extracts every fact into a SQLite database, and answers precise numerical questions by querying that database.

**Deadline:** August 13, 12 PM IST (~5 days from now)  
**Validation dataset release:** August 10, 3 PM IST (~2 days from now)  
**Approach:** Database-First (Approach A) — pre-extract everything, then answer deterministically  
**LLM:** Google Gemini 2.0 Flash (free tier: 1,500 req/day, 15 RPM)  
**Storage:** SQLite (`company.db`)  
**Language:** Python 3.12  

---

## Proposed Changes

### Phase 0: Project Setup & Environment

#### [NEW] `requirements.txt`
Install all dependencies in a virtual environment:
```
google-generativeai    # Gemini API client
pdfplumber             # PDF text/table extraction (fallback & validation)
openpyxl               # Excel workbook parsing
pandas                 # Data manipulation
tqdm                   # Progress bars
```

#### [NEW] `.env`
```
GEMINI_API_KEY=<your-key>
```

#### [NEW] `src/` directory structure
```
BITS-Hackathon-Dataset/
├── src/
│   ├── __init__.py
│   ├── config.py                  # API keys, paths, constants
│   ├── extract_pdfs.py            # Phase 1: PDF → JSON via Gemini
│   ├── extract_workbooks.py       # Phase 1: Excel → JSON via openpyxl
│   ├── normalize.py               # Phase 2: Clean & standardize values
│   ├── build_db.py                # Phase 3: Load JSON → SQLite
│   ├── answer_engine.py           # Phase 4: Question → Number
│   ├── money.py                   # Currency parsing utilities
│   └── run_pipeline.py            # Master orchestrator
├── data/
│   ├── extracted/                 # Raw JSON from each document
│   └── company.db                 # The final SQLite database
├── submission.jsonl               # Final answers
```

---

### Phase 1: Document Extraction (Days 1-2)

> [!IMPORTANT]
> This is the most critical phase. If extraction is wrong, everything downstream fails. The BRIEFING warns: *"a default extraction can silently return a fraction of the page."*

#### [NEW] [extract_pdfs.py](file:///c:/Users/adits/Downloads/BITS-Hackathon-Dataset/src/extract_pdfs.py) — Core extraction script

**Strategy:** Send each PDF to Gemini 2.0 Flash with a **document-type-specific JSON schema** prompt. Gemini sees the visual layout natively, avoiding the silent-data-loss problem.

**Document-type-specific schemas** (the key insight — each doc type needs a different extraction template):

| Document Type (count) | Key Fields to Extract | Priority |
|---|---|---|
| `completion_certificate` (155) | project_name, client_name, contract_value, completion_date, commencement_date, performance_grading, role (Prime/Sub), work_category, signing_officer | 🔴 Critical |
| `company_completion_certificate` (155) | project_name, client_name, contract_value, completion_date, commencement_date, work_category, role | 🔴 Critical |
| `reference_letter` (132) | project_name, client_name, doc_id | 🟡 High — needed for absence queries |
| `personnel_certificate` (48) | person_name, cert_type (PMP/Six Sigma/etc), cert_id, issue_date | 🔴 Critical |
| `cv` (39) | person_name, projects_led (list of project names with roles) | 🔴 Critical |
| `performance_bond` (60) | project_name, client_name, bond_value, bank_name, contract_value | 🟡 High |
| `compliance_matrix` (40) | project_name, tender_ref, compliance_items | 🟢 Medium |
| `general_ledger_book` (8) | year, entries (project, invoice_amount, received_amount, dates) | 🟡 High |
| `bank_statement` (8) | year, transactions (date, description, amount, balance) | 🟡 High |
| `financial_statement` (7) | year, revenue, profit, assets, liabilities, key_ratios | 🟡 High |
| `ra_bill` / `final_ra_bill` (12) | project_name, bill_items, quantities, rates, totals | 🟢 Medium |
| `tender_dossier` (6) | tender_name, project_name, bid_value, client | 🟢 Medium |
| `iso_certificate` (5) | cert_type, cert_number, validity_dates | 🟢 Lower |
| `annual_report` (2) | year, key_figures, project_lists, employee_counts | 🟡 High |
| `past_performance_portfolio` (1) | all projects listed with values and clients | 🔴 Critical — master index |

**Rate limit management:**
- Process at **12 RPM** (leaving headroom below the 15 RPM limit)
- **5-second delay** between requests
- Exponential backoff on 429 errors (wait 60s, then retry)
- Save each result to `data/extracted/{doc_id}.json` immediately (crash-safe)
- Skip already-extracted docs on re-run (idempotent)

**Processing order** (priority-first):
1. `past_performance_portfolio` (1 doc — may contain a master project list!)
2. `completion_certificate` (155 — the core data)
3. `personnel_certificate` (48 — engineer↔cert links)
4. `cv` (39 — engineer↔project links)
5. `reference_letter` (132 — presence/absence tracking)
6. `company_completion_certificate` (155 — cross-validation)
7. Everything else

**Estimated API usage:** 678 calls ÷ 12/min = ~57 minutes runtime. Well within 1 day.

#### [NEW] [extract_workbooks.py](file:///c:/Users/adits/Downloads/BITS-Hackathon-Dataset/src/extract_workbooks.py) — Excel extraction

**No LLM needed.** Use `openpyxl` directly:
- Read all sheets including "Notes" sheets
- Evaluate formulas where possible (or read cached values)
- Handle the 6 BOQ workbooks, ageing, trial balance, and asset register
- Output to `data/extracted/` as JSON

---

### Phase 2: Data Normalization (Day 2-3)

#### [NEW] [money.py](file:///c:/Users/adits/Downloads/BITS-Hackathon-Dataset/src/money.py) — Indian currency parser

> [!IMPORTANT]
> This is a **precision-critical** module. The BRIEFING explicitly states: *"Reading money back out is a parsing problem with a correct answer, not an approximation."* Getting this wrong cascades into every monetary answer.

Must handle all formats from the BRIEFING:
```python
parse_indian_money("INR 33.38 Cr")      → 333800000
parse_indian_money("3,338.00 Lakh")     → 333800000  
parse_indian_money("33,38,00,000")      → 333800000
parse_indian_money("333800000")         → 333800000
parse_indian_money("₹33.38 Crore")      → 333800000
parse_indian_money("Rs. 33,38,00,000")  → 333800000
```

Also handle edge cases:
- "seventy-three crore" in prose → 730000000 (questions use words!)
- Lakh vs Lakhs, Crore vs Cr vs Crores
- Negative values, "Nil", missing values
- Values with "approximately" or "~" prefix

#### [NEW] [normalize.py](file:///c:/Users/adits/Downloads/BITS-Hackathon-Dataset/src/normalize.py) — Entity resolution & cleaning

1. **Client name canonicalization:** Map all variations to a single canonical form
   - Build a fuzzy-match dictionary after first pass
   - e.g., "PWD, Govt of Maharashtra" = "Public Works Department, Govt of Maharashtra"
2. **Project name standardization:** Ensure "Ring Road — Maharashtra Pkg-125" matches across docs
3. **Date parsing:** All dates → `YYYY-MM-DD` format
4. **Work category normalization:** Standardize categories (highway, flyover, WTP, etc.)

---

### Phase 3: Database Construction (Day 3)

#### [NEW] [build_db.py](file:///c:/Users/adits/Downloads/BITS-Hackathon-Dataset/src/build_db.py) — SQLite schema & loading

**Database schema** — designed to support all 21 question patterns:

```sql
-- Core entity tables
CREATE TABLE clients (
    client_id       INTEGER PRIMARY KEY,
    client_name     TEXT UNIQUE NOT NULL
);

CREATE TABLE engineers (
    engineer_id     INTEGER PRIMARY KEY,
    name            TEXT NOT NULL
);

CREATE TABLE works (
    work_id              INTEGER PRIMARY KEY,
    project_name         TEXT NOT NULL,
    client_id            INTEGER REFERENCES clients(client_id),
    contract_value       INTEGER,          -- always in raw rupees
    completion_date      TEXT,             -- YYYY-MM-DD
    commencement_date    TEXT,
    work_category        TEXT,             -- highway, flyover, WTP, etc.
    performance_grading  TEXT,             -- Excellent, Very Good, Satisfactory, etc.
    role                 TEXT,             -- Prime, Sub-contractor
    signing_officer      TEXT,
    has_reference_letter BOOLEAN DEFAULT 0,
    has_performance_bond BOOLEAN DEFAULT 0,
    doc_cc_id            TEXT,             -- completion_certificate doc_id
    doc_ccc_id           TEXT,             -- company_completion_certificate doc_id
    doc_ref_id           TEXT              -- reference_letter doc_id (NULL if absent)
);

-- Engineer ↔ Work many-to-many relationship  
CREATE TABLE engineer_works (
    engineer_id     INTEGER REFERENCES engineers(engineer_id),
    work_id         INTEGER REFERENCES works(work_id),
    role_on_project TEXT,                  -- Project Manager, Site Engineer, etc.
    PRIMARY KEY (engineer_id, work_id)
);

-- Engineer ↔ Certificate relationship
CREATE TABLE engineer_certs (
    engineer_id     INTEGER REFERENCES engineers(engineer_id),
    cert_type       TEXT NOT NULL,         -- PMP, Six Sigma Black Belt, etc.
    cert_id         TEXT,                  -- PMI-200029, etc.
    issue_date      TEXT,
    doc_id          TEXT
);

-- Performance bonds
CREATE TABLE bonds (
    bond_id         INTEGER PRIMARY KEY,
    work_id         INTEGER REFERENCES works(work_id),
    bond_value      INTEGER,
    bank_name       TEXT,
    doc_id          TEXT
);

-- Financial data (from financial statements, ledgers, bank statements)
CREATE TABLE financials (
    year            INTEGER,
    doc_type        TEXT,                  -- financial_statement, general_ledger, bank_statement
    metric          TEXT,                  -- revenue, profit, total_assets, etc.
    value           REAL,
    doc_id          TEXT
);

-- Ledger entries (from general_ledger_book)
CREATE TABLE ledger_entries (
    entry_id        INTEGER PRIMARY KEY,
    year            INTEGER,
    date            TEXT,
    project_name    TEXT,
    work_id         INTEGER REFERENCES works(work_id),
    entry_type      TEXT,                  -- invoice, receipt, credit_note
    amount          INTEGER,
    doc_id          TEXT
);

-- BOQ line items (from workbooks)
CREATE TABLE boq_items (
    item_id         INTEGER PRIMARY KEY,
    contract_ref    TEXT,                  -- Contract_71, etc.
    item_desc       TEXT,
    quantity         REAL,
    rate            REAL,
    amount          REAL
);

-- Asset register (from workbook)
CREATE TABLE assets (
    asset_id        INTEGER PRIMARY KEY,
    description     TEXT,
    acquisition_cost REAL,
    acquisition_date TEXT
);

-- ISO certificates
CREATE TABLE iso_certs (
    cert_id         TEXT PRIMARY KEY,
    cert_type       TEXT,
    valid_from      TEXT,
    valid_to        TEXT,
    doc_id          TEXT
);
```

**Post-load validation queries:**
```sql
-- Must return 155
SELECT COUNT(*) FROM works;

-- Must return 62
SELECT COUNT(DISTINCT client_name) FROM clients;

-- Verify reference letter gap: 155 works - 132 with letters = 23 without
SELECT COUNT(*) FROM works WHERE has_reference_letter = 0;

-- Cross-validate contract values against company completion certificates
```

---

### Phase 4: Question Answering Engine (Days 3-4)

#### [NEW] [answer_engine.py](file:///c:/Users/adits/Downloads/BITS-Hackathon-Dataset/src/answer_engine.py)

**Two-stage architecture:**

**Stage 1 — LLM Question Parser** (1 Gemini call per question):
Send the question text to Gemini with a structured output schema:
```python
class ParsedQuestion(BaseModel):
    question_shape: str           # one of the 21+ patterns
    client_name: Optional[str]
    engineer_name: Optional[str]
    cert_type: Optional[str]      # PMP, Six Sigma, etc.
    cert_id: Optional[str]        # PMI-200029, etc.
    project_name: Optional[str]
    filter_grading: Optional[str] # Excellent, Satisfactory, etc.
    filter_role: Optional[str]    # Prime, Sub
    exclude_category: Optional[str]
    threshold_value: Optional[int]  # parsed from "seventy-three crore" etc.
    target_value: Optional[int]     # for gap_to_threshold
    date_reference: Optional[str]
    aggregation: str              # sum, count, average, max, min, difference, percentage
```

**Stage 2 — Deterministic Query Executor** (pure Python/SQL, no LLM):

Implement a handler for each known question shape:

| Shape | SQL/Logic Pattern |
|---|---|
| `absence` | `SELECT COUNT(*) FROM works w JOIN clients c ON ... WHERE c.client_name = ? AND w.has_reference_letter = 0` |
| `date_span` | `SELECT julianday(w.completion_date) - julianday(ec.issue_date) FROM ...` |
| `distinct_count` | `SELECT COUNT(DISTINCT w.work_category) FROM works w JOIN engineer_works ew ON ...` |
| `hop_aggregate` | Multi-hop: engineer→cert→project→client→all_client_works→SUM(value) |
| `temporal_chain` | Filter works by completion_date > cert.issue_date, then SUM |
| `avg_work_size` | `SELECT AVG(contract_value) FROM works WHERE client_id = ?` |
| `doc_filtered_aggregate` | `SELECT SUM(contract_value) FROM works WHERE client_id = ? AND performance_grading = ?` |
| `exclusion_aggregate` | `SELECT SUM(contract_value) FROM works WHERE client_id = ? AND work_category != ?` |
| `gap_to_threshold` | `target - SUM(contract_value)` |
| `rank_value` | `ORDER BY contract_value DESC LIMIT 2`, compute difference |
| `referenced_share` | `COUNT(has_reference_letter=1) / COUNT(*) * 100` |
| `role_split` | `SELECT SUM(contract_value) FROM works WHERE client_id = ? AND role = ?` |
| `threshold_aggregate` | `SELECT SUM(contract_value) FROM works WHERE client_id = ? AND contract_value >= ?` |

**Handling the 8 unseen patterns:**
These likely involve:
- Financial statement queries (revenue, profit trends)
- Ledger/bank statement questions (receivables, cash flow)
- BOQ workbook queries (bill-of-quantity totals)
- Asset register queries (plant & machinery values)
- ISO certificate validity queries
- Tender dossier bid values
- Annual report figures
- Cross-document financial reconciliation

We'll build a **fallback handler** that:
1. Identifies the relevant data tables
2. Constructs a SQL query from the parsed parameters
3. If no pattern matches, uses Gemini to generate the SQL directly from the question + schema

---

### Phase 5: Validation & Submission (Days 4-5)

#### [NEW] [run_pipeline.py](file:///c:/Users/adits/Downloads/BITS-Hackathon-Dataset/src/run_pipeline.py) — Master orchestrator

```python
# Step 1: Extract all documents
python src/extract_pdfs.py
python src/extract_workbooks.py

# Step 2: Normalize
python src/normalize.py

# Step 3: Build database
python src/build_db.py

# Step 4: Answer sample questions
python src/answer_engine.py --questions sample_questions.json --output submission.jsonl

# Step 5: Score
python evaluate.py --submission submission.jsonl --per-question
```

**Target:** 25/25 on sample set before August 10th validation release.

After August 10th (validation dataset release):
- Run answer_engine on validation questions
- Identify failing patterns
- Add handlers for unseen question shapes
- Re-score and iterate

---

## Key Hidden Details & Traps Identified

> [!CAUTION]
> These are specific traps embedded in the dataset that will catch naive implementations:

1. **The "defensible average" trap (HS-IC-0011):** "Average size across all completed projects for the commissioning client" — you must find ALL projects for that client, not just the one named. The answer uses 3 values, not 1.

2. **The "absence" trap (HS-IC-0001/0002):** Proving a reference letter is *missing* requires knowing the full set of works for a client. A retrieval system reports zero because it never saw the missing thing.

3. **The "seventy-three crore" trap (HS-IC-0024):** Threshold values are written in words ("seventy-three crore mark"), not digits. The money parser must handle prose numbers.

4. **The percentage trap (evaluate.py L41-44):** Percentages are graded as **counts** (exact or off-by-one), not as "within 2%". So `66.67` must be answered as exactly `66.67`, not `66.6666...` or `67`.

5. **The "as Prime" filter (HS-IC-0022/0023):** Some questions filter by role. But notice HS-IC-0023 for "Jharkhand Municipal Corporation as Prime" = 384,100,000 = sum of ALL 3 works (87.4+314.6+69.5 = 471.5M ≠ 384.1M). Wait — only 2 of 3 are "as Prime". The role field is per-work, not per-client.

6. **The doc_id ≠ work_id mapping:** DOC-CC-001 is completion certificate #1, but that does NOT mean it's about Work #1. The mapping must come from reading the document content.

7. **Financial statement format changes:** The BRIEFING says *"the reporting layout shifts over the period covered."* Older statements (2019-2020) will have different structures than newer ones (2024-2025).

8. **Excel formulas:** Workbooks contain `=SUM(...)` formulas. Reading cached values via `openpyxl` with `data_only=True` may return `None` if the workbook was never opened in Excel. We may need to evaluate formulas or use `xlcalc`.

9. **Indian digit grouping:** `33,38,00,000` is NOT 33 billion. In Indian numbering: `33` crore, `38` lakh, `00` thousand, `000` = 333,800,000. The regex must handle 2-digit groups after the first 3 digits.

10. **The "Notes" sheets in workbooks:** The BRIEFING specifically mentions workbooks have "Notes sheets." These may contain values unreachable elsewhere.

11. **132 reference letters for 155 works:** The gap of 23 works without reference letters is **deliberate and queryable**. Our `has_reference_letter` boolean must be set by actually checking which works have a matching reference letter, not by assuming sequential numbering.

12. **Performance bond numbering is NOT sequential:** Bond numbers jump (DOC-BOND-00005, 00006, 00010, 00016...). Only 60 bonds exist for 155 works. Not every work has a bond.

13. **Personnel certificate numbering gap:** PCERT goes from 006-044, then jumps to 156-164. There's a gap from 045-155. This is likely by design.

---

## Day-by-Day Timeline

| Day | Date | Goal | Deliverable |
|---|---|---|---|
| **Day 1** | Aug 8 (Fri) | Setup + Extract priority docs | `extract_pdfs.py` running, ~300 docs processed |
| **Day 2** | Aug 9 (Sat) | Complete extraction + normalization | All 687 docs extracted, `money.py` done |
| **Day 3** | Aug 10 (Sun) | Build DB + answer engine v1 | `company.db` populated, **score on sample set** |
| **Day 4** | Aug 11 (Mon) | Validation dataset arrives 3PM, iterate | Handle failing patterns, add unseen shapes |
| **Day 5** | Aug 12 (Tue) | Final fixes + generate submission | **`submission.jsonl` ready** |
| **Deadline** | Aug 13 12PM | Submit | ✅ |

---

## Verification Plan

### Automated Tests
```bash
# 1. Verify the scorer works
python evaluate.py --self-test

# 2. Score our answers against the 25 sample questions  
python evaluate.py --submission submission.jsonl --per-question

# 3. Validate DB integrity
python -c "import sqlite3; conn=sqlite3.connect('data/company.db'); print(conn.execute('SELECT COUNT(*) FROM works').fetchone())"
```

### Manual Verification
- Cross-check 5 randomly selected completion certificates against their extracted JSON
- Verify all 3 "Jal Nigam, Jharkhand" works match expected values (730200000, 814400000, 69200000)
- Confirm reference letter count: 132 letters mapped to the correct 132 works
- Run all 25 sample questions and verify each step matches the `reasoning_steps`

---

## Open Questions

> [!IMPORTANT]
> **Q1:** The hidden test set has "21 reasoning patterns" but we've only seen 13. Do you want me to proactively build handlers for likely unseen patterns (financial queries, BOQ queries, asset queries) even before we see the validation set? **My recommendation:** Yes — we should extract ALL document types into the DB regardless, so we're ready for anything.

> [!IMPORTANT]  
> **Q2:** For the presentation (if you advance): The problem statement mentions *"Winners present their solutions in a 20-minute session."* Even though you said you want to focus on the technical solution, should I document the architecture cleanly enough that you *could* present if needed? **My recommendation:** I'll keep the code well-commented and modular either way — it's good practice.
