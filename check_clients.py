import json, sqlite3, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from src.answer_engine import parse_question, answer_question, find_client_id

conn = sqlite3.connect('data/company.db')

for qpath in ['BITS-Validation-Dataset/questions.json', 'questions.json']:
    try:
        with open(qpath, 'r', encoding='utf-8') as f:
            vdata = json.load(f)
        vquestions = vdata.get('questions', vdata) if isinstance(vdata, dict) else vdata
        break
    except FileNotFoundError:
        continue

# Check every question for PHED West Bengal mention
print("=== Questions mentioning PHED West Bengal ===")
for q in vquestions:
    qid = q['qid']
    question = q['question']
    qlow = question.lower()
    if 'west bengal' in qlow and ('public health' in qlow or 'phed' in qlow or 'phe ' in qlow):
        params = parse_question(conn, question)
        ans = answer_question(conn, question, qid, answer_type=q.get('answer_type'))
        client = params.get('client_name')
        client_id = find_client_id(conn, client) if client else None
        print(f"\n  [{qid}] shape={params['question_shape']}")
        print(f"    client='{client}' -> client_id={client_id}")
        print(f"    ans={ans}")
        print(f"    Q: {question[:150]}")

# Also look at the broader pattern: any question where find_client_id maps to the wrong client
print("\n\n=== Questions where client_id lookup might be wrong ===")
# Get all client names from DB
all_clients = {r[0]: r[1] for r in conn.execute("SELECT client_id, client_name FROM clients").fetchall()}

problem_count = 0
for q in vquestions:
    qid = q['qid']
    question = q['question']
    params = parse_question(conn, question)
    client = params.get('client_name')
    if not client:
        continue
    
    cid = find_client_id(conn, client)
    if cid and all_clients.get(cid, '').lower() != client.lower():
        # The resolved client_id maps to a different name!
        ans = answer_question(conn, question, qid, answer_type=q.get('answer_type'))
        print(f"\n  [{qid}] MISMATCH!")
        print(f"    Parsed client: '{client}'")
        print(f"    client_id={cid} -> actual name: '{all_clients.get(cid)}'")
        print(f"    shape={params['question_shape']}, ans={ans}")
        print(f"    Q: {question[:120]}")
        problem_count += 1

print(f"\nTotal client_id mismatches: {problem_count}")

conn.close()
