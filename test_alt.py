import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import src.answer_engine as ae

conn = sqlite3.connect('data/company.db')

for qpath in ['BITS-Validation-Dataset/questions.json', 'questions.json']:
    try:
        with open(qpath, 'r', encoding='utf-8') as f:
            vdata = json.load(f)
        vquestions = vdata.get('questions', vdata) if isinstance(vdata, dict) else vdata
        break
    except FileNotFoundError:
        continue

# Base answers with 'unbilled_abs' only
ae.VARIANTS = {'unbilled_abs'}
base_answers = {}
for q in vquestions:
    base_answers[q['qid']] = ae.answer_question(conn, q['question'], q['qid'], answer_type=q.get('answer_type'))

# Now test with engineer_client_alt
ae.VARIANTS = {'unbilled_abs', 'engineer_client_alt'}
changed = []
for q in vquestions:
    qid = q['qid']
    ans = ae.answer_question(conn, q['question'], qid, answer_type=q.get('answer_type'))
    if ans != base_answers[qid]:
        changed.append((qid, base_answers[qid], ans, q['question']))

print(f"engineer_client_alt changes {len(changed)} questions")
for qid, old, new, q in changed:
    print(f"  [{qid}] {old} -> {new}")
    print(f"    Q: {q[:120]}")

conn.close()
