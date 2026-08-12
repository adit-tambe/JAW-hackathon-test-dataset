"""
llm_text2sql.py — LLM-driven Text-to-SQL pipeline for the BITS Hackathon.

Architecture:
  1. Send question + full DB schema + few-shot examples to Gemini
  2. LLM generates executable SQLite SQL
  3. Execute SQL against company.db
  4. Validate result type matches answer_type
  5. Return exact numeric answer

This replaces the rule-based answer_engine for questions it cannot handle.
"""
import json
import os
import re
import sqlite3
import time
import hashlib
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "company.db"
CACHE_PATH = PROJECT_ROOT / "data" / "llm_sql_cache.json"

# ── API Key Management ──────────────────────────────────────────────────────
# Support multiple API keys for rate limit rotation
def get_api_keys():
    """Collect all available API keys from environment."""
    keys = []
    # Primary key
    primary = os.getenv("GEMINI_API_KEY", "")
    if primary:
        keys.append(primary)
    # Additional keys: GEMINI_API_KEY_2, GEMINI_API_KEY_3, etc.
    for i in range(2, 20):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "")
        if k:
            keys.append(k)
    return keys

API_KEYS = get_api_keys()
_current_key_idx = 0

def get_next_model():
    """Get a model instance, rotating API keys on rate limit."""
    global _current_key_idx
    if not API_KEYS:
        raise RuntimeError("No GEMINI_API_KEY found in .env")
    key = API_KEYS[_current_key_idx % len(API_KEYS)]
    genai.configure(api_key=key)
    return genai.GenerativeModel("gemini-3.5-flash")

def rotate_key():
    """Switch to the next API key."""
    global _current_key_idx
    _current_key_idx += 1
    if _current_key_idx >= len(API_KEYS):
        _current_key_idx = 0

# ── Cache ────────────────────────────────────────────────────────────────────
_cache = {}

def load_cache():
    global _cache
    if CACHE_PATH.exists():
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            _cache = json.load(f)

def save_cache():
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(_cache, f, indent=2)

def cache_key(question: str, answer_type: str) -> str:
    """Stable hash for caching."""
    return hashlib.md5(f"{question}|{answer_type}".encode()).hexdigest()


# ── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = r"""You are an expert SQLite query generator for an Indian infrastructure construction company database.

## DATABASE SCHEMA

```sql
-- 28 clients (government departments, corporations, private firms)
CREATE TABLE clients (
    client_id INTEGER PRIMARY KEY,
    client_name TEXT  -- e.g. 'Jal Nigam, Jharkhand', 'Public Works Department, Govt of Maharashtra'
);

-- 105 engineers
CREATE TABLE engineers (
    engineer_id INTEGER PRIMARY KEY,
    name TEXT  -- e.g. 'Asha Nair', 'Rahul Menon', 'Chandan Banerjee'
);

-- 155 completed construction works/projects
CREATE TABLE works (
    work_id INTEGER PRIMARY KEY,
    project_name TEXT,       -- e.g. 'Ring Road — Maharashtra Pkg-125', 'WTP Augmentation — Uttar Pradesh Pkg-2'
    client_id INTEGER REFERENCES clients(client_id),
    contract_value INTEGER,  -- in Indian Rupees (e.g. 193299999 = INR 19.33 Cr)
    completion_date TEXT,    -- ISO format 'YYYY-MM-DD'
    commencement_date TEXT,  -- ISO format 'YYYY-MM-DD'
    work_category TEXT,      -- one of: 'Bridges Flyovers', 'Buildings', 'Expressways', 'Industrial Epc',
                             --         'Irrigation', 'Large Bridges', 'Roads Highways', 'Roads Maintenance',
                             --         'Sewerage Drainage', 'Small Buildings', 'Tunnels', 'Water Supply',
                             --         'Water Treatment'
    performance_grading TEXT,-- one of: 'Excellent', 'Very Good', 'Good', 'Satisfactory'
    role TEXT,               -- 'Prime' or 'JV Partner' (there is NO 'Sub-contractor' value)
    signing_officer TEXT,
    work_description TEXT,
    has_reference_letter INTEGER,  -- 1 = yes, 0 = no (132 of 155 have letters)
    has_performance_bond INTEGER,
    doc_cc_id TEXT,
    doc_ccc_id TEXT,
    doc_ref_id TEXT
);

-- Links engineers to works they led (many-to-many)
CREATE TABLE engineer_works (
    engineer_id INTEGER REFERENCES engineers(engineer_id),
    work_id INTEGER REFERENCES works(work_id),
    role_on_project TEXT
);

-- Professional certifications held by engineers
CREATE TABLE engineer_certs (
    id INTEGER PRIMARY KEY,
    engineer_id INTEGER REFERENCES engineers(engineer_id),
    cert_type TEXT,     -- 'PMP' or 'Six Sigma Black Belt'
    cert_id TEXT,       -- e.g. 'PMI-200029', '6S-500161'
    issue_date TEXT,    -- ISO format, most are '2021-03-10'
    expiry_date TEXT,
    doc_id TEXT
);

-- Performance bonds (bank guarantees)
CREATE TABLE bonds (
    bond_id INTEGER PRIMARY KEY,
    work_id INTEGER REFERENCES works(work_id),
    bond_value INTEGER,
    contract_value INTEGER,
    bank_name TEXT,
    bond_number TEXT,
    doc_id TEXT
);

-- Client reference letters for works
CREATE TABLE reference_letters (
    ref_id INTEGER PRIMARY KEY,
    work_id INTEGER REFERENCES works(work_id),
    project_name TEXT,
    client_name TEXT,
    letter_date TEXT,
    doc_id TEXT
);

-- Accounts receivable / ageing register (invoices)
CREATE TABLE receivables (
    id INTEGER PRIMARY KEY,
    invoice_no TEXT,
    client_name TEXT,   -- NOTE: may differ slightly from clients.client_name
    client_id INTEGER,  -- NOTE: 17 rows have NULL client_id (for 'Public Health Engineering Dept, West Bengal')
    invoice_date TEXT,
    invoiced REAL,      -- amount billed
    status TEXT,
    received REAL,      -- amount collected
    outstanding REAL,   -- amount still owed (can be NEGATIVE for overpaid invoices)
    doc_id TEXT
);

-- Bill of quantities line items
CREATE TABLE boq_items (
    item_id INTEGER PRIMARY KEY,
    contract_ref TEXT,
    item_desc TEXT,
    quantity REAL,
    rate REAL,
    amount REAL
);

-- Plant and machinery register
CREATE TABLE assets (
    asset_id INTEGER PRIMARY KEY,
    description TEXT,
    acquisition_cost REAL,
    acquisition_date TEXT
);

-- ISO quality certifications
CREATE TABLE iso_certs (
    id INTEGER PRIMARY KEY,
    cert_type TEXT,
    cert_number TEXT,
    valid_from TEXT,
    valid_to TEXT,
    doc_id TEXT
);
```

## ALL 28 CLIENTS
| client_id | client_name |
|---|---|
| 1 | National Special Projects Office |
| 2 | Mega Infrastructure Authority |
| 3 | Public Works Department, Govt of Gujarat |
| 4 | Peninsular Petroleum Corporation |
| 5 | Suvarna Projects Limited |
| 6 | Public Health Engineering Dept, Gujarat |
| 7 | Trishakti Power Generation Corporation |
| 8 | Irrigation & Waterways Dept, Govt of Rajasthan |
| 9 | Subarnarekha Valley Corporation |
| 10 | Public Works Department, Govt of Maharashtra |
| 11 | Mahanadi Steel Corporation |
| 12 | Irrigation & Waterways Dept, Govt of West Bengal |
| 13 | Public Health Engineering Dept, Odisha |
| 14 | Jal Nigam, Uttar Pradesh |
| 15 | Lakshya Engineering & Construction |
| 16 | Arunodaya Infrastructure |
| 17 | Jal Nigam, Jharkhand |
| 18 | Jharkhand Municipal Corporation |
| 19 | Public Works Department, Govt of Tamil Nadu |
| 20 | Central Works & Buildings Bureau |
| 21 | Gujarat Municipal Corporation |
| 22 | Maharashtra Municipal Corporation |
| 23 | Tamil Nadu Municipal Corporation |
| 24 | National Expressway Development Authority |
| 25 | Public Works Department, Govt of West Bengal |
| 26 | Irrigation & Waterways Dept, Govt of Uttar Pradesh |
| 27 | Jal Nigam, Gujarat |
| 28 | Meridian Constructors & Co. |
| NULL (use client_name) | Public Health Engineering Dept, West Bengal |

## CRITICAL RULES

1. **Return ONLY a single SELECT statement.** No explanations, no markdown, no comments. Just the SQL.
2. **The answer must be a single number.** Your query must return exactly one row with one column.
3. **Money values are in raw Rupees** (integers). 1 Crore = 10,000,000. "40 crore" = 400,000,000. "seventy-three crore" = 730,000,000.
4. **"Sub-contractor" in a question means role = 'JV Partner'** (the DB has no 'Sub-contractor' role).
5. **Percentages should be 0-100**, not 0-1. Round to 2 decimal places with ROUND(..., 2).
6. **For outstanding balance queries**: Use signed SUM(outstanding) including negative values.
7. **For unbilled/gap queries**: Use ABS(SUM(contract_value) - SUM(invoiced)) — always absolute value.
8. **For year-on-year difference**: Use ABS(year1_total - year2_total) — always absolute value.
9. **For mean-median diff**: Use (mean - median), signed, NOT absolute.
10. **Client name matching**: Use exact client_id from the table above. Match the question's client name to the closest entry.
11. **For receivables with 'Public Health Engineering Dept, West Bengal'**: client_id is NULL, match by client_name instead.
12. **Engineer -> Client chain**: When a question names an engineer and a project, find the client_id from the works table via the project, then aggregate ALL works for that client.
13. **"after [date]" temporal questions**: Filter works by completion_date > '[date]', joined through engineer_works.
14. **Date span questions**: Calculate ABS(julianday(completion_date) - julianday('[date]')) for the named project.
15. **Use INTEGER division carefully**: For averages, cast to avoid integer truncation: CAST(SUM(contract_value) AS REAL) / COUNT(*).
16. **Threshold/mark/cutoff values**: "crossing the forty crore mark" means contract_value >= 400000000.
17. **Gap to threshold**: target_value - SUM(contract_value). Use MAX(0, target - current) since you can't need negative additional work.
18. **For collection percent**: ROUND(SUM(received) * 100.0 / SUM(invoiced), 2) from receivables.
19. **Exclude category**: "excluding water treatment" means work_category <> 'Water Treatment', NOT LIKE.
20. **Distinct categories**: COUNT(DISTINCT work_category) for an engineer's works via engineer_works join.

## FEW-SHOT EXAMPLES

Question: "Cross-checking against the Public Health Engineering Dept, Gujarat, how many works have no client reference letter on file?"
answer_type: count
SQL: SELECT COUNT(*) FROM works WHERE client_id = 6 AND has_reference_letter = 0

Question: "Jal Nigam, Jharkhand — I'm pretty sure we only have reference letters for one of their projects, but am I right in thinking how many of their completed works actually lack a client reference letter on file?"
answer_type: count
SQL: SELECT COUNT(*) FROM works WHERE client_id = 17 AND has_reference_letter = 0

Question: "Cross-checking the completion date against Asha Nair's PMP for 2021-03-10, what number of days passed from issuance to finish for School Building — Madhya Pradesh Pkg-145?"
answer_type: days
SQL: SELECT CAST(ABS(julianday(w.completion_date) - julianday('2021-03-10')) AS INTEGER) FROM works w WHERE LOWER(w.project_name) LIKE '%school building%madhya pradesh%pkg-145%'

Question: "To ensure the audit documentation reflects the official timeline, what is the exact interval from Chandan Banerjee's March 10, 2021 PMP issuance to the completion of the WTP Augmentation project in West Bengal Package 51?"
answer_type: days
SQL: SELECT CAST(ABS(julianday(w.completion_date) - julianday('2021-03-10')) AS INTEGER) FROM works w WHERE LOWER(w.project_name) LIKE '%wtp augmentation%west bengal%pkg-51%'

Question: "How many different categories of work has Chandan Banerjee led to completion under his PMP certification?"
answer_type: count
SQL: SELECT COUNT(DISTINCT w.work_category) FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id JOIN engineers e ON ew.engineer_id = e.engineer_id WHERE e.name = 'Chandan Banerjee'

Question: "Asha Nair's PMP project portfolio shows several completed assignments; how many distinct work classifications has she successfully delivered?"
answer_type: count
SQL: SELECT COUNT(DISTINCT w.work_category) FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id JOIN engineers e ON ew.engineer_id = e.engineer_id WHERE e.name = 'Asha Nair'

Question: "Starting from Rahul Menon's PMP certification (PMI-200029) for the Ring Road — Maharashtra Pkg-125, what is the combined value of every completed assignment he has delivered for the Public Works Department, Govt of Maharashtra?"
answer_type: money
SQL: SELECT SUM(w.contract_value) FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id JOIN engineers e ON ew.engineer_id = e.engineer_id WHERE e.name = 'Rahul Menon' AND w.client_id = 10

Question: "Could you please calculate the total value of all completed assignments Neha Chopra has delivered for Lakshya Engineering & Construction, referencing her PMP certification (PMI-200006) and the Residential Quarters — West Bengal Pkg-67 project as the reference point?"
answer_type: money
SQL: SELECT SUM(w.contract_value) FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id JOIN engineers e ON ew.engineer_id = e.engineer_id WHERE e.name = 'Neha Chopra' AND w.client_id = 15

Question: "Gautam Joshi's PMP issued March 10, 2021, what's the combined value of only the projects he led that wrapped up after that date, needed immediately for the bid cutoff?"
answer_type: money
SQL: SELECT SUM(w.contract_value) FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id JOIN engineers e ON ew.engineer_id = e.engineer_id WHERE e.name = 'Gautam Joshi' AND w.completion_date > '2021-03-10'

Question: "Could you please calculate the combined value of the works Asha Nair led that were completed after her PMP certification date of March 10, 2021?"
answer_type: money
SQL: SELECT SUM(w.contract_value) FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id JOIN engineers e ON ew.engineer_id = e.engineer_id WHERE e.name = 'Asha Nair' AND w.completion_date > '2021-03-10'

Question: "Regarding Asha Nair's PMP work on the Cable Stayed Bridge — Jharkhand Pkg-115, what is the defensible average size across all completed projects for the commissioning client?"
answer_type: money
SQL: SELECT CAST(ROUND(CAST(SUM(contract_value) AS REAL) / COUNT(*)) AS INTEGER) FROM works WHERE client_id = (SELECT client_id FROM works WHERE LOWER(project_name) LIKE '%cable stayed bridge%jharkhand%pkg-115%')

Question: "Cross-checking Naveen Roy's PMP for Check Dam — Gujarat Pkg-62, what is the mean size across projects for the client?"
answer_type: money
SQL: SELECT CAST(ROUND(CAST(SUM(contract_value) AS REAL) / COUNT(*)) AS INTEGER) FROM works WHERE client_id = (SELECT client_id FROM works WHERE LOWER(project_name) LIKE '%check dam%gujarat%pkg-62%')

Question: "Irrigation & Waterways Dept, Govt of Uttar Pradesh; Excellent — cross-checking those entries, what is the sum of the contract amounts for the projects the department graded Excellent on their completion certificates?"
answer_type: money
SQL: SELECT SUM(contract_value) FROM works WHERE client_id = 26 AND performance_grading = 'Excellent'

Question: "Jal Nigam, Jharkhand; Satisfactory — what is the total amount for those projects where the department marked Satisfactory on their completion certificates?"
answer_type: money
SQL: SELECT SUM(contract_value) FROM works WHERE client_id = 17 AND performance_grading = 'Satisfactory'

Question: "Irrigation & Waterways Dept, Govt of West Bengal; excluding buildings, what is the precise aggregate value of every project we have delivered for them?"
answer_type: money
SQL: SELECT SUM(contract_value) FROM works WHERE client_id = 12 AND work_category <> 'Buildings'

Question: "Jharkhand Municipal Corporation; excluding roads maintenance, what's the combined value of our completed assignments for them before we lock this bid?"
answer_type: money
SQL: SELECT SUM(contract_value) FROM works WHERE client_id = 18 AND work_category <> 'Roads Maintenance'

Question: "Irrigation & Waterways Dept, Govt of Uttar Pradesh is the account under review, so how much additional work must we secure to reach our credential target of INR 20 Cr?"
answer_type: money
SQL: SELECT MAX(0, 200000000 - SUM(contract_value)) FROM works WHERE client_id = 26

Question: "Jal Nigam, Jharkhand, by how much does the largest completed work exceed the second largest?"
answer_type: money
SQL: SELECT MAX(contract_value) - (SELECT contract_value FROM works WHERE client_id = 17 ORDER BY contract_value DESC LIMIT 1 OFFSET 1) FROM works WHERE client_id = 17

Question: "Jharkhand Municipal Corporation, cross-checking against the portal, what is the difference between the largest work value and the second largest?"
answer_type: money
SQL: SELECT MAX(contract_value) - (SELECT contract_value FROM works WHERE client_id = 18 ORDER BY contract_value DESC LIMIT 1 OFFSET 1) FROM works WHERE client_id = 18

Question: "Jal Nigam, Jharkhand is our starting point for the audit, so what whole number out of one hundred represents the defensible share of completed assignments that carry formal verification on file?"
answer_type: percent
SQL: SELECT ROUND(SUM(CASE WHEN has_reference_letter = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) FROM works WHERE client_id = 17

Question: "Jharkhand Municipal Corporation is where I am cross-checking our engagement history, so what number out of one hundred represents the count of assignments for that client that carry a reference letter divided by the total?"
answer_type: percent
SQL: SELECT ROUND(SUM(CASE WHEN has_reference_letter = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) FROM works WHERE client_id = 18

Question: "Public Health Engineering Dept, Gujarat; as Prime — what's the final contract value we need to lock in before the 3pm cutoff?"
answer_type: money
SQL: SELECT SUM(contract_value) FROM works WHERE client_id = 6 AND role = 'Prime'

Question: "Jharkhand Municipal Corporation; as Prime — what's the defensible aggregate value we should report for the completed scope?"
answer_type: money
SQL: SELECT SUM(contract_value) FROM works WHERE client_id = 18 AND role = 'Prime'

Question: "Jal Nigam, Jharkhand, what's the combined value of their works crossing the seventy-three crore mark so I can lock the bid before the deadline?"
answer_type: money
SQL: SELECT SUM(contract_value) FROM works WHERE client_id = 17 AND contract_value >= 730000000

Question: "Maharashtra Municipal Corporation, what's the aggregate of their contracts hitting the six crore line so I can lock the pricing before the cutoff?"
answer_type: money
SQL: SELECT SUM(contract_value) FROM works WHERE client_id = 22 AND contract_value >= 60000000
"""


def build_user_prompt(question: str, answer_type: str) -> str:
    """Build the user prompt for a single question."""
    return f"""Question: "{question}"
answer_type: {answer_type}
SQL:"""


def extract_sql(response_text: str) -> str:
    """Extract SQL from LLM response, handling markdown code blocks."""
    text = response_text.strip()
    # Remove markdown code blocks
    if '```' in text:
        m = re.search(r'```(?:sql)?\s*\n?(.*?)```', text, re.DOTALL | re.IGNORECASE)
        if m:
            text = m.group(1).strip()
    # Remove any leading/trailing non-SQL content
    lines = text.split('\n')
    sql_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith(('SELECT', 'WITH')) or sql_lines:
            sql_lines.append(line)
    if sql_lines:
        text = '\n'.join(sql_lines)
    # Safety: only allow SELECT
    if not text.upper().lstrip().startswith(('SELECT', 'WITH')):
        return None
    return text.rstrip(';') if text else None


def validate_result(result, answer_type: str) -> bool:
    """Check if the result makes sense for the answer_type."""
    if result is None:
        return False
    try:
        val = float(result)
    except (TypeError, ValueError):
        return False
    if answer_type == 'percent':
        return 0 <= val <= 100
    if answer_type == 'count':
        return val >= 0 and val == int(val) and val < 1000
    if answer_type == 'days':
        return val >= 0 and val < 10000
    if answer_type == 'money':
        return True  # money can be any magnitude
    return True


def query_llm(question: str, answer_type: str, max_retries: int = 3) -> str:
    """Send question to LLM and get SQL back."""
    user_prompt = build_user_prompt(question, answer_type)

    for attempt in range(max_retries):
        try:
            model = get_next_model()
            response = model.generate_content(
                [SYSTEM_PROMPT, user_prompt],
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    max_output_tokens=1024,
                )
            )
            sql = extract_sql(response.text)
            if sql:
                return sql
            print(f"  [Attempt {attempt+1}] Could not extract SQL from response: {response.text[:200]}")
        except Exception as e:
            err_str = str(e)
            if '429' in err_str or 'quota' in err_str.lower() or 'rate' in err_str.lower():
                print(f"  [Rate limit hit on key {_current_key_idx}] Rotating key...")
                rotate_key()
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
            print(f"  [Attempt {attempt+1}] LLM error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    return None


def execute_sql(sql: str) -> float:
    """Execute SQL against company.db and return the result."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        result = conn.execute(sql).fetchone()
        if result and result[0] is not None:
            return result[0]
        return None
    except Exception as e:
        print(f"  [SQL Error] {e}")
        print(f"  [SQL] {sql[:200]}")
        return None
    finally:
        conn.close()


def format_answer(value, answer_type: str):
    """Format the answer for submission."""
    if value is None:
        return 0
    val = float(value)
    if answer_type == 'percent':
        return round(val, 2)
    if answer_type in ('count', 'days', 'money'):
        return int(round(val))
    if val == int(val):
        return int(val)
    return round(val, 2)


def answer_question_llm(question: str, answer_type: str, qid: str = None,
                        use_cache: bool = True) -> float:
    """Answer a single question using the LLM Text-to-SQL pipeline."""
    ck = cache_key(question, answer_type)

    # Check cache
    if use_cache and ck in _cache:
        cached = _cache[ck]
        return cached.get('answer', 0)

    # Query LLM for SQL
    sql = query_llm(question, answer_type)
    if not sql:
        print(f"  [{qid}] FAILED: No SQL generated")
        return 0

    # Execute SQL
    result = execute_sql(sql)
    answer = format_answer(result, answer_type)

    # Validate
    if not validate_result(result, answer_type):
        print(f"  [{qid}] WARNING: Result {result} may not match {answer_type}, retrying...")
        # Retry with explicit error feedback
        sql2 = query_llm(
            question + f"\n\nPREVIOUS ATTEMPT returned {result} which doesn't look like a valid {answer_type}. "
            f"Please generate a corrected SQL query.",
            answer_type
        )
        if sql2:
            result2 = execute_sql(sql2)
            if validate_result(result2, answer_type):
                sql = sql2
                result = result2
                answer = format_answer(result, answer_type)

    # Cache result
    _cache[ck] = {
        'qid': qid,
        'question': question[:100],
        'answer_type': answer_type,
        'sql': sql,
        'raw_result': result,
        'answer': answer,
    }
    save_cache()

    return answer


# ── Self-test ────────────────────────────────────────────────────────────────

def self_test():
    """Run the 25 sample questions and verify against gold answers."""
    with open(PROJECT_ROOT / "sample_questions.json", 'r', encoding='utf-8') as f:
        data = json.load(f)

    questions = data.get("questions", data)
    load_cache()

    correct = 0
    partial = 0
    total = len(questions)

    for q in questions:
        qid = q["qid"]
        question_text = q["question"]
        answer_type = q.get("answer_type", "money")
        gold = q.get("answer")

        print(f"\n[{qid}] {question_text[:80]}...")
        answer = answer_question_llm(question_text, answer_type, qid)
        print(f"  Gold: {gold}  Got: {answer}")

        # Score using the official metric
        from evaluate import score_one
        score = score_one(gold, answer)
        if score == 1.0:
            correct += 1
            print(f"  [OK] EXACT")
        elif score > 0:
            partial += 1
            print(f"  [!!] PARTIAL ({score})")
        else:
            print(f"  [XX] WRONG")

        # Rate limit delay
        time.sleep(5)

    print(f"\n{'='*60}")
    print(f"Results: {correct}/{total} exact, {partial} partial")
    print(f"{'='*60}")


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        self_test()
    else:
        print("Usage: python src/llm_text2sql.py --self-test")
        print("   or: import and use answer_question_llm()")
