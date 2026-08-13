"""Find questions carrying a qualifier the handler for their shape ignores."""
import json, csv, sqlite3, sys, re, collections
sys.path.insert(0, '.')
from src.answer_engine import parse_question, reconcile_shape
from src.config import DB_PATH

qs = json.load(open('validation_questions.json', encoding='utf-8'))['questions']
conn = sqlite3.connect(DB_PATH)

# which params each shape's handler actually consumes
USES = {
 'category_difference': {'client_name','cat1','cat2'},
 'yearly_diff':        {'client_name','year1','year2'},
 'threshold_aggregate':{'client_name','threshold_value','target_value'},
 'exclusion_aggregate':{'client_name','exclude_category'},
 'general_aggregate':  {'client_name'},
 'avg_work_size':      {'client_name'},
 'mean_median_diff':   {'client_name'},
 'rank_value':         {'client_name'},
 'distinct_count':     {'engineer_name'},
 'referenced_share':   {'client_name'},
 'absence':            {'client_name'},
 'collection_percent': {'client_name'},
 'outstanding_balance':{'client_name'},
 'unbilled_gap':       {'client_name'},
 'gap_to_threshold':   {'client_name','threshold_value','target_value'},
 'date_span':          {'project_name','engineer_name','cert_type','date_reference'},
 'temporal_chain':     {'engineer_name','cert_type','date_reference'},
}
PARAMS = ['cat1','cat2','year1','year2','threshold_value','target_value','exclude_category']

print('=== A. parsed qualifier that the shape handler ignores ===')
hits = collections.Counter(); rows = []
for q in qs:
    p = parse_question(conn, q['question'])
    sh = reconcile_shape(p['question_shape'], q.get('answer_type'), p)
    used = USES.get(sh, set())
    extra = [k for k in PARAMS if p.get(k) and k not in used]
    if extra:
        hits[sh] += 1
        rows.append((q['qid'], sh, extra, {k: p.get(k) for k in extra}, q['question']))
for qid, sh, extra, vals, text in rows:
    print(f'  {qid}  {sh:22s} ignores {vals}')
    print(f'      {text.encode("ascii","replace").decode()[:170]}')
print(f'  -> {len(rows)} questions;  by shape: {dict(hits)}')

print()
print('=== B. question mentions a year/crore figure but no such param was parsed ===')
n = 0
for q in qs:
    p = parse_question(conn, q['question'])
    sh = reconcile_shape(p['question_shape'], q.get('answer_type'), p)
    t = q['question']
    yrs = set(re.findall(r'\b(20[0-2]\d)\b', t)) - {'2021'}      # 2021 = the PMP issue year
    cr  = re.findall(r'(\d[\d.,]*)\s*(?:cr\b|crore)', t, re.I)
    miss = []
    if yrs and not (p.get('year1') or p.get('year2')): miss.append(f'years {sorted(yrs)}')
    if cr and not (p.get('threshold_value') or p.get('target_value')): miss.append(f'crore {cr}')
    if miss:
        n += 1
        print(f'  {q["qid"]}  {sh:22s} unparsed: {miss}')
        print(f'      {t.encode("ascii","replace").decode()[:170]}')
print(f'  -> {n} questions')
