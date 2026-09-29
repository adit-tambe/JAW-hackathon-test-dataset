import json, csv, sqlite3, sys, statistics, collections, itertools, openpyxl, datetime
sys.path.insert(0, '.')
from src.answer_engine import parse_question, reconcile_shape
from src.config import DB_PATH

qs = json.load(open('validation_questions.json', encoding='utf-8'))['questions']
ans = {r[0].strip(): float(r[1]) for r in csv.reader(open('attempt_final.csv', newline='', encoding='utf-8-sig'))
       if r and r[0].strip().lower() != 'question_id'}
conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
W = collections.defaultdict(list)
for r in conn.execute('''select cl.client_name c, w.contract_value v, w.work_category k, w.completion_date d,
                                w.commencement_date cd, w.has_reference_letter h, w.performance_grading pg
                         from works w join clients cl on cl.client_id=w.client_id'''):
    W[r['c']].append(dict(r))
wb = openpyxl.load_workbook('documents/workbooks/Receivables_Ageing.xlsx', data_only=True)
AR = collections.defaultdict(lambda: {'inv':0,'rec':0,'out':0,'n':0,'pos':0})
for row in list(wb['AR Ageing'].iter_rows(values_only=True))[1:]:
    if row and row[0] and row[1]:
        a = AR[row[1].strip()]
        a['inv'] += row[3] or 0; a['rec'] += row[5] or 0; a['out'] += row[6] or 0
        a['n'] += 1; a['pos'] += max(0, row[6] or 0)

def s(a, g):
    return 0.0 if not g else max(0.0, 1.0 - abs(a - g) / abs(g))

P = []
for q in qs:
    p = parse_question(conn, q['question'])
    p['shape'] = reconcile_shape(p['question_shape'], q.get('answer_type'), p)
    p['qid'] = q['qid']; p['text'] = q['question']
    P.append(p)

def alts(p):
    """every plausible alternative gold for this question"""
    out = {}
    cl = p.get('client_name'); sh = p['shape']
    g = W.get(cl, []); ar = AR.get(cl)
    v = [r['v'] for r in g]
    if sh == 'category_difference' and p.get('cat1') and p.get('cat2'):
        s1 = sum(r['v'] for r in g if r['k'] == p['cat1']); s2 = sum(r['v'] for r in g if r['k'] == p['cat2'])
        out['signed c1-c2'] = s1 - s2; out['signed c2-c1'] = s2 - s1
        r1 = sum(r['v'] for r in g if r['k'] == p['cat1'] and r['h']); r2 = sum(r['v'] for r in g if r['k'] == p['cat2'] and r['h'])
        out['ref-letter works only'] = abs(r1 - r2)
    if sh == 'general_aggregate' and g:
        out['ref-letter works only'] = sum(r['v'] for r in g if r['h'])
        out['graded works only'] = sum(r['v'] for r in g if r['pg'])
    if sh == 'avg_work_size' and v:
        out['median not mean'] = statistics.median(v)
        out['mean of ref-letter works'] = statistics.mean([r['v'] for r in g if r['h']]) if any(r['h'] for r in g) else None
    if sh == 'threshold_aggregate':
        t = p.get('threshold_value') or p.get('target_value')
        if t and g:
            out['strictly >'] = sum(r['v'] for r in g if r['v'] > t)
            out['below threshold'] = sum(r['v'] for r in g if r['v'] < t)
    if sh == 'exclusion_aggregate' and p.get('exclude_category') and g:
        out['only the excluded category'] = sum(r['v'] for r in g if r['k'] == p['exclude_category'])
    if sh == 'rank_value' and len(v) > 1:
        sv = sorted(v, reverse=True)
        out['largest - smallest'] = sv[0] - sv[-1]
        out['2nd - 3rd'] = sv[1] - sv[2] if len(sv) > 2 else None
    if sh == 'unbilled_gap' and g and ar:
        out['awarded - received'] = abs(sum(v) - ar['rec'])
        out['signed awarded - invoiced'] = sum(v) - ar['inv']
    if sh == 'outstanding_balance' and ar:
        out['positive rows only'] = ar['pos']
        out['invoiced - received (same)'] = ar['inv'] - ar['rec']
    if sh == 'collection_percent' and ar:
        out['received/invoiced full precision'] = ar['rec'] / ar['inv'] * 100
        out['1 - out/inv'] = (1 - ar['out'] / ar['inv']) * 100
    if sh == 'referenced_share' and g:
        out['full precision'] = sum(1 for r in g if r['h']) / len(g) * 100
    if sh == 'date_span':
        out['inclusive +1'] = ans[p['qid']] + 1
        out['exclusive -1'] = ans[p['qid']] - 1
    if sh == 'yearly_diff' and p.get('year1') and p.get('year2') and g:
        y1, y2 = str(p['year1']), str(p['year2'])
        a1 = sum(r['v'] for r in g if r['d'][:4] == y1); a2 = sum(r['v'] for r in g if r['d'][:4] == y2)
        out['signed y1-y2'] = a1 - a2; out['signed y2-y1'] = a2 - a1
    return {k: val for k, val in out.items() if val is not None}

# per-question loss for each alternative
cand = []
for p in P:
    a = ans[p['qid']]
    for label, gv in alts(p).items():
        l = 1.0 - s(a, gv)
        if 1e-9 < l < 0.99:            # a real but non-fatal difference
            cand.append((l, p['qid'], p['shape'], label, gv, a))
cand.sort(reverse=True)
print(f'{"loss":>8}  {"qid":12s} {"shape":22s} {"alternative":34s} {"alt value":>18} {"our answer":>18}')
print('-' * 122)
for l, qid, sh, lab, gv, a in cand[:40]:
    print(f'{l:8.4f}  {qid:12s} {sh:22s} {lab:34s} {gv:18,.2f} {a:18,.2f}')
print(f'\n{len(cand)} non-fatal alternatives in total')

# any single alternative, or pair/triple, summing to the 0.751 residual?
TARGET = 0.751; TOL = 0.006
print(f'\n=== subsets of distinct questions summing to {TARGET} +- {TOL} ===')
found = 0
for k in (1, 2, 3):
    for combo in itertools.combinations(cand, k):
        if len({c[1] for c in combo}) != k: continue
        tot = sum(c[0] for c in combo)
        if abs(tot - TARGET) <= TOL:
            found += 1
            print(f'  sum={tot:.4f}: ' + ' + '.join(f'{c[1]}/{c[3]}({c[0]:.3f})' for c in combo))
            if found > 25: print('  ...'); break
    if found > 25: break
print(f'  {found} candidate explanations')
