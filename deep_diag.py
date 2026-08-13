"""
deep_diag.py — Deep diagnostic: for each validation question,
compare the rule engine answer with an independently computed SQL answer
to find discrepancies. No LLM needed — we hand-code the SQL for each shape.
"""
import json
import sqlite3
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.answer_engine import (
    answer_question, parse_question, find_client_id, find_engineer_id,
    normalize_text, canonical_category, question_role, question_grading,
    VARIANTS, variant
)
from src.money import format_as_answer

conn = sqlite3.connect('data/company.db')

# Load validation questions
for qpath in ['BITS-Validation-Dataset/questions.json', 'questions.json']:
    try:
        with open(qpath, 'r', encoding='utf-8') as f:
            vdata = json.load(f)
        vquestions = vdata.get('questions', vdata) if isinstance(vdata, dict) else vdata
        break
    except FileNotFoundError:
        continue

# Check for questions where the parse is suspicious
print("=" * 80)
print(f"DEEP DIAGNOSTIC — {len(vquestions)} validation questions")
print(f"Current VARIANTS: {VARIANTS}")
print("=" * 80)

suspicious = []

for i, q in enumerate(vquestions):
    qid = q['qid']
    question = q['question']
    answer_type = q.get('answer_type', 'money')
    
    params = parse_question(conn, question)
    shape = params['question_shape']
    client = params.get('client_name')
    eng = params.get('engineer_name')
    proj = params.get('project_name')
    cat1 = params.get('cat1')
    cat2 = params.get('cat2')
    exclude = params.get('exclude_category')
    threshold = params.get('threshold_value')
    target = params.get('target_value')
    year1 = params.get('year1')
    year2 = params.get('year2')
    date_ref = params.get('date_reference')
    qlow = params.get('qlow', '')
    
    ans = answer_question(conn, question, qid, answer_type=answer_type)
    
    # Check 1: Client not found in DB
    client_id = find_client_id(conn, client) if client else None
    if shape not in ('date_span', 'distinct_count', 'temporal_chain') and not client_id and client:
        suspicious.append((qid, 'CLIENT_NOT_FOUND', f"client='{client}' not in DB", ans, question[:100]))
    
    # Check 2: Category difference with missing categories
    if shape == 'category_difference' and (not cat1 or not cat2):
        suspicious.append((qid, 'MISSING_CATEGORY', f"cat1={cat1} cat2={cat2}", ans, question[:100]))
    
    # Check 3: Exclusion with unknown category
    if shape == 'exclusion_aggregate' and exclude:
        canon = canonical_category(exclude)
        if not canon:
            suspicious.append((qid, 'UNKNOWN_EXCLUDE_CAT', f"exclude='{exclude}'", ans, question[:100]))
    
    # Check 4: Threshold parsing issues
    if shape in ('threshold_aggregate', 'gap_to_threshold') and not threshold and not target:
        suspicious.append((qid, 'NO_THRESHOLD', f"threshold={threshold} target={target}", ans, question[:100]))
    
    # Check 5: Year diff with missing years
    if shape == 'yearly_diff' and (not year1 or not year2):
        suspicious.append((qid, 'MISSING_YEARS', f"y1={year1} y2={year2}", ans, question[:100]))
    
    # Check 6: Date span with no date reference
    if shape == 'date_span' and not date_ref:
        suspicious.append((qid, 'NO_DATE_REF', f"date_ref={date_ref}", ans, question[:100]))
    
    # Check 7: Role split answers that are None
    if shape == 'role_split':
        role = question_role(qlow)
        if client_id:
            direct = conn.execute(
                "SELECT SUM(contract_value) FROM works WHERE client_id = ? AND role = ? AND contract_value IS NOT NULL",
                (client_id, role)).fetchone()[0]
            if direct is None:
                suspicious.append((qid, 'ROLE_NO_MATCH', f"client={client} role={role}", ans, question[:100]))
    
    # Check 8: Doc filtered with unknown grading
    if shape == 'doc_filtered_aggregate':
        grading = question_grading(qlow)
        if not grading:
            suspicious.append((qid, 'NO_GRADING', f"grading=None", ans, question[:100]))
    
    # Check 9: Verify category_difference computation
    if shape == 'category_difference' and cat1 and cat2 and client_id:
        v1 = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND LOWER(work_category) = LOWER(?)", (client_id, cat1)).fetchone()[0] or 0
        v2 = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND LOWER(work_category) = LOWER(?)", (client_id, cat2)).fetchone()[0] or 0
        expected = abs(v1 - v2)
        formatted = format_as_answer(expected)
        if formatted != ans:
            suspicious.append((qid, 'CATDIFF_MISMATCH', f"v1={v1} v2={v2} expected={formatted} got={ans}", ans, question[:100]))
    
    # Check 10: Answer is negative (might be wrong sign)
    if isinstance(ans, (int, float)) and ans < 0:
        suspicious.append((qid, 'NEGATIVE_ANSWER', f"ans={ans}", ans, question[:100]))
    
    # Check 11: Shape reconciliation might be wrong
    # If answer_type says 'count' but we computed money, or vice versa
    if answer_type == 'count' and isinstance(ans, (int, float)) and ans > 1000:
        suspicious.append((qid, 'COUNT_TOO_LARGE', f"type=count ans={ans}", ans, question[:100]))
    if answer_type == 'days' and isinstance(ans, (int, float)) and ans > 5000:
        suspicious.append((qid, 'DAYS_TOO_LARGE', f"type=days ans={ans}", ans, question[:100]))
    if answer_type == 'percent' and isinstance(ans, (int, float)) and ans > 100:
        suspicious.append((qid, 'PCT_OVER_100', f"type=percent ans={ans}", ans, question[:100]))

    # Check 12: 'outstanding' questions - verify the receivables lookup
    if shape == 'outstanding_balance' and client:
        # Try by client_name directly
        res1 = conn.execute("SELECT SUM(outstanding) FROM receivables WHERE LOWER(client_name) = LOWER(?)", (client,)).fetchone()[0]
        # Try by client_id
        res2 = None
        if client_id:
            res2 = conn.execute("SELECT SUM(outstanding) FROM receivables WHERE client_id = ?", (client_id,)).fetchone()[0]
            if not res2:
                c_row = conn.execute("SELECT client_name FROM clients WHERE client_id = ?", (client_id,)).fetchone()
                if c_row:
                    res2 = conn.execute("SELECT SUM(outstanding) FROM receivables WHERE LOWER(client_name) = LOWER(?)", (c_row[0],)).fetchone()[0]
        
        if res1 is not None and res2 is not None and res1 != res2:
            suspicious.append((qid, 'RECEIVABLES_MISMATCH', f"by_name={res1} by_id={res2} client='{client}'", ans, question[:100]))

print(f"\nSuspicious questions: {len(suspicious)}")
print("=" * 80)
for qid, issue, detail, ans, q in suspicious:
    print(f"  [{qid}] {issue}: {detail}")
    print(f"    ans={ans}")
    print(f"    Q: {q}")
    print()

conn.close()
