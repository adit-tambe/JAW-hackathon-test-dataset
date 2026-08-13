import json, sqlite3, sys
from pathlib import Path
from statistics import mean, median
sys.path.insert(0, str(Path(__file__).parent))
from src.answer_engine import parse_question, answer_question, find_client_id, find_engineer_id

conn = sqlite3.connect('data/company.db')
vdata = json.load(open('BITS-Validation-Dataset/questions.json'))
vqs = {q['qid']: q for q in vdata['questions']}

# HV-IC-0276 - negative mean_median_diff
q = vqs['HV-IC-0276']
print(f"Q: {q['question'][:300]}")
print(f"answer_type: {q.get('answer_type')}")

params = parse_question(conn, q['question'])
print(f"Shape: {params['question_shape']}")
print(f"Client: {params.get('client_name')}")
print(f"Engineer: {params.get('engineer_name')}")

eng_id = find_engineer_id(conn, params.get('engineer_name'))
print(f"Engineer ID: {eng_id}")

# The question says "Meera Roy" and is about mean-median diff
# But what client does it resolve to?
client_id = find_client_id(conn, params.get('client_name'))
print(f"Client ID: {client_id}")

if client_id:
    vals = [r[0] for r in conn.execute("SELECT contract_value FROM works WHERE client_id = ? AND contract_value IS NOT NULL", (client_id,)).fetchall()]
    print(f"Works count: {len(vals)}")
    print(f"Values: {vals}")
    m = mean(vals)
    med = median(vals)
    print(f"Mean: {m}, Median: {med}")
    print(f"mean - median = {m - med}")
    print(f"Rounded: {int(round(m - med))}")

ans = answer_question(conn, q['question'], q['qid'], answer_type=q.get('answer_type'))
print(f"\nOur answer: {ans}")

# HV-IC-0412 - negative outstanding
q2 = vqs['HV-IC-0412']
print(f"\n\n{'='*60}")
print(f"Q: {q2['question'][:300]}")
print(f"answer_type: {q2.get('answer_type')}")
params2 = parse_question(conn, q2['question'])
print(f"Shape: {params2['question_shape']}")
print(f"Client: {params2.get('client_name')}")

# Check Maharashtra Municipal Corp receivables
rows = conn.execute("SELECT invoice_no, invoiced, received, outstanding FROM receivables WHERE client_name LIKE '%Maharashtra Municipal%'").fetchall()
print(f"\nReceivables rows: {len(rows)}")
for r in rows:
    print(f"  inv={r[0]}, invoiced={r[1]}, received={r[2]}, outstanding={r[3]}")
total = sum(r[3] for r in rows)
print(f"Total outstanding: {total}")

ans2 = answer_question(conn, q2['question'], q2['qid'], answer_type=q2.get('answer_type'))
print(f"Our answer: {ans2}")

conn.close()
