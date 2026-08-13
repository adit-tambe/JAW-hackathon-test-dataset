import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from src.answer_engine import parse_question, answer_question, find_client_id, reconcile_shape

conn = sqlite3.connect('data/company.db')

for qpath in ['BITS-Validation-Dataset/questions.json', 'questions.json']:
    try:
        with open(qpath, 'r', encoding='utf-8') as f:
            vdata = json.load(f)
        vquestions = vdata.get('questions', vdata) if isinstance(vdata, dict) else vdata
        break
    except FileNotFoundError:
        continue

# Find the two problematic questions
for q in vquestions:
    if q['qid'] in ('HV-IC-0045', 'HV-IC-0371'):
        qid = q['qid']
        question = q['question']
        answer_type = q.get('answer_type', 'money')
        
        params = parse_question(conn, question)
        shape_before = params['question_shape']
        shape_after = reconcile_shape(shape_before, answer_type, params)
        
        print(f"\n{'='*60}")
        print(f"[{qid}] answer_type={answer_type}")
        print(f"Q: {question[:200]}")
        print(f"Shape BEFORE reconcile: {shape_before}")
        print(f"Shape AFTER reconcile:  {shape_after}")
        print(f"Client: {params.get('client_name')}")
        print(f"Engineer: {params.get('engineer_name')}")
        print(f"Project: {params.get('project_name')}")
        print(f"Cat1/Cat2: {params.get('cat1')} / {params.get('cat2')}")
        
        client_id = find_client_id(conn, params.get('client_name'))
        print(f"client_id: {client_id}")
        
        # Check receivables
        if client_id:
            row = conn.execute("SELECT SUM(invoiced), SUM(received) FROM receivables WHERE client_id = ?", (client_id,)).fetchone()
            print(f"Receivables (by client_id={client_id}): invoiced={row[0]}, received={row[1]}")
            if row[0] and row[0] > 0:
                pct = round((row[1] / row[0]) * 100.0, 2)
                print(f"Collection %: {pct}")
        
        ans = answer_question(conn, question, qid, answer_type=answer_type)
        print(f"Our answer: {ans}")

# Also check ALL collection_percent questions
print("\n\n" + "=" * 60)
print("ALL collection_percent questions")
print("=" * 60)
for q in vquestions:
    params = parse_question(conn, q['question'])
    if params['question_shape'] == 'collection_percent':
        ans = answer_question(conn, q['question'], q['qid'], answer_type=q.get('answer_type'))
        print(f"  [{q['qid']}] type={q.get('answer_type')} ans={ans} client={params.get('client_name')}")

conn.close()
