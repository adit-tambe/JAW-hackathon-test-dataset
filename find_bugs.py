"""
find_bugs.py — Exhaustive bug finder for all answer shapes.
For each shape, independently compute the answer using direct SQL,
then compare with our engine's answer. This catches computation bugs
that the variant flags can't fix.
"""
import json, sqlite3, sys, re
from pathlib import Path
from datetime import datetime
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).parent))
from src.answer_engine import (
    answer_question, parse_question, find_client_id, find_engineer_id,
    normalize_text, canonical_category, question_role, question_grading, variant
)
from src.money import format_as_answer

conn = sqlite3.connect('data/company.db')

for qpath in ['BITS-Validation-Dataset/questions.json', 'questions.json']:
    try:
        with open(qpath, 'r', encoding='utf-8') as f:
            vdata = json.load(f)
        vquestions = vdata.get('questions', vdata) if isinstance(vdata, dict) else vdata
        break
    except FileNotFoundError:
        continue

mismatches = []

for q in vquestions:
    qid = q['qid']
    question = q['question']
    answer_type = q.get('answer_type', 'money')
    
    params = parse_question(conn, question)
    shape = params['question_shape']
    client = params.get('client_name')
    client_id = find_client_id(conn, client) if client else None
    eng = params.get('engineer_name')
    eng_id = find_engineer_id(conn, eng) if eng else None
    cat1 = params.get('cat1')
    cat2 = params.get('cat2')
    exclude = params.get('exclude_category')
    threshold = params.get('threshold_value')
    target = params.get('target_value')
    year1 = params.get('year1')
    year2 = params.get('year2')
    qlow = params.get('qlow', '')
    
    our_ans = answer_question(conn, question, qid, answer_type=answer_type)
    
    # Independently verify specific shapes
    verify_ans = None
    
    if shape == 'collection_percent' and client_id:
        # Verify collection percent
        row = conn.execute("SELECT SUM(invoiced), SUM(received) FROM receivables WHERE client_id = ?", (client_id,)).fetchone()
        if row and row[0] and row[0] > 0:
            pct = (row[1] / row[0]) * 100.0
            verify_ans = round(pct, 2)
        else:
            # Try by name
            c_row = conn.execute("SELECT client_name FROM clients WHERE client_id = ?", (client_id,)).fetchone()
            if c_row:
                row = conn.execute("SELECT SUM(invoiced), SUM(received) FROM receivables WHERE LOWER(client_name) = LOWER(?)", (c_row[0],)).fetchone()
                if row and row[0] and row[0] > 0:
                    pct = (row[1] / row[0]) * 100.0
                    verify_ans = round(pct, 2)
    
    elif shape == 'collection_percent' and client:
        # PHED WB case - no client_id in DB
        row = conn.execute("SELECT SUM(invoiced), SUM(received) FROM receivables WHERE LOWER(client_name) = LOWER(?)", (client,)).fetchone()
        if row and row[0] and row[0] > 0:
            pct = (row[1] / row[0]) * 100.0
            verify_ans = round(pct, 2)
    
    elif shape == 'unbilled_gap' and client_id:
        awarded = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND contract_value IS NOT NULL", (client_id,)).fetchone()[0] or 0
        # Try invoiced by client_id first
        invoiced = conn.execute("SELECT SUM(invoiced) FROM receivables WHERE client_id = ?", (client_id,)).fetchone()[0]
        if not invoiced:
            c_row = conn.execute("SELECT client_name FROM clients WHERE client_id = ?", (client_id,)).fetchone()
            if c_row:
                invoiced = conn.execute("SELECT SUM(invoiced) FROM receivables WHERE LOWER(client_name) = LOWER(?)", (c_row[0],)).fetchone()[0] or 0
        gap = awarded - (invoiced or 0)
        verify_ans = format_as_answer(abs(gap) if variant("unbilled_abs") else gap)
    
    if verify_ans is not None and verify_ans != our_ans:
        mismatches.append((qid, shape, our_ans, verify_ans, question[:120]))

# Now let's also look at ALL questions and check for suspicious patterns in each shape
print("=" * 80)
print("SHAPE-BY-SHAPE ANSWER DISTRIBUTION")
print("=" * 80)

from collections import defaultdict
shape_answers = defaultdict(list)
for q in vquestions:
    qid = q['qid']
    question = q['question']
    answer_type = q.get('answer_type', 'money')
    params = parse_question(conn, question)
    shape = params['question_shape']
    ans = answer_question(conn, question, qid, answer_type=answer_type)
    shape_answers[shape].append((qid, ans, answer_type, question[:80]))

for shape in sorted(shape_answers.keys()):
    items = shape_answers[shape]
    answers = [a for _, a, _, _ in items]
    negative = [a for a in answers if isinstance(a, (int, float)) and a < 0]
    zeros = [a for a in answers if a == 0]
    print(f"\n{shape} ({len(items)} questions):")
    if negative:
        print(f"  NEGATIVES: {len(negative)} — {negative}")
    if zeros:
        print(f"  ZEROS: {len(zeros)} — might be failures")
    # Check for answer_type mismatches
    for qid, ans, atype, qtxt in items:
        if atype == 'percent' and isinstance(ans, (int, float)) and (ans > 100 or ans < 0):
            print(f"  [{qid}] PERCENT OUT OF RANGE: {ans}")
        if atype == 'count' and isinstance(ans, (int, float)) and ans > 100:
            print(f"  [{qid}] COUNT VERY HIGH: {ans}")
        if atype == 'days' and isinstance(ans, (int, float)) and (ans > 5000 or ans < 0):
            print(f"  [{qid}] DAYS SUSPICIOUS: {ans}")

print(f"\n\nVerification mismatches: {len(mismatches)}")
for qid, shape, ours, verified, q in mismatches:
    print(f"  [{qid}] shape={shape}: ours={ours} verified={verified}")
    print(f"    Q: {q}")

conn.close()
