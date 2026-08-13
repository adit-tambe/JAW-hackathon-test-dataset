import json, csv, sqlite3, sys, re, collections
sys.path.insert(0, '.')
from src.answer_engine import parse_question, reconcile_shape
from src.config import DB_PATH

WORDS = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,
         'nine':9,'ten':10,'eleven':11,'twelve':12}
qs = json.load(open('validation_questions.json', encoding='utf-8'))['questions']
ans = {r[0].strip(): float(r[1]) for r in csv.reader(open('attempt_final.csv', newline='', encoding='utf-8-sig'))
       if r and r[0].strip().lower() != 'question_id'}
conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row

byclient = collections.defaultdict(list)
for r in conn.execute('''select cl.client_name c, w.client_office o, w.work_id i, w.contract_value v
                         from works w join clients cl on cl.client_id=w.client_id'''):
    byclient[r['c']].append(dict(r))

pat = re.compile(r'\b(' + '|'.join(WORDS) + r'|\d{1,2})\s+(?:completed\s+|awarded\s+|finished\s+|closed\s+)?'
                 r'(?:jobs?|works?|projects?|assignments?|contracts?|entries)\b', re.I)
print('Questions that state a work count:')
hits = 0
for q in qs:
    m = pat.search(q['question'])
    if not m: continue
    tok = m.group(1).lower()
    n = WORDS.get(tok, None) or (int(tok) if tok.isdigit() else None)
    if n is None: continue
    p = parse_question(conn, q['question'])
    sh = reconcile_shape(p['question_shape'], q.get('answer_type'), p)
    cl = p.get('client_name')
    g = byclient.get(cl, [])
    offices = collections.Counter(x['o'] for x in g)
    match_client = (len(g) == n)
    match_office = [o for o, k in offices.items() if k == n]
    hits += 1
    flag = 'client-level MATCHES' if match_client else ('client-level is %d' % len(g))
    print(f'\n  {q["qid"]}  [{sh}]  says "{m.group(0)}" -> n={n}   ans={ans[q["qid"]]:,.0f}')
    print(f'      client={cl}  ({len(g)} works)  {flag}')
    print(f'      offices: {dict(offices)}   office(s) with exactly {n}: {match_office if match_office else "none"}')
    print(f'      {q["question"].encode("ascii","replace").decode()[:165]}')
print(f'\n{hits} questions state a count')
