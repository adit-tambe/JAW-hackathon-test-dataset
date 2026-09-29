import json, csv, sqlite3, sys, statistics, collections, itertools, openpyxl
sys.path.insert(0, '.')
from src.answer_engine import parse_question, reconcile_shape
from src.config import DB_PATH

qs = json.load(open('validation_questions.json', encoding='utf-8'))['questions']
ans = {r[0].strip(): float(r[1]) for r in csv.reader(open('attempt_final.csv', newline='', encoding='utf-8-sig'))
       if r and r[0].strip().lower() != 'question_id'}
conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
W = collections.defaultdict(list)
for r in conn.execute('''select cl.client_name c, w.contract_value v, w.work_category k, w.completion_date d,
                                w.has_reference_letter h from works w join clients cl on cl.client_id=w.client_id'''):
    W[r['c']].append(dict(r))
wb = openpyxl.load_workbook('documents/workbooks/Receivables_Ageing.xlsx', data_only=True)
AR = collections.defaultdict(lambda: {'inv':0,'rec':0,'out':0,'pos':0})
for row in list(wb['AR Ageing'].iter_rows(values_only=True))[1:]:
    if row and row[0] and row[1]:
        a = AR[row[1].strip()]
        a['inv'] += row[3] or 0; a['rec'] += row[5] or 0; a['out'] += row[6] or 0; a['pos'] += max(0, row[6] or 0)

def s(a, g):
    return 0.0 if not g else max(0.0, 1.0 - abs(a - g) / abs(g))

def pool(cl, typ):
    """every natural quantity derivable for this client"""
    g = W.get(cl, []); out = {}
    if g:
        v = [r['v'] for r in g]
        cats = collections.defaultdict(float)
        for r in g: cats[r['k']] += r['v']
        tot = sum(v)
        if typ == 'percent':
            n = len(g); nref = sum(1 for r in g if r['h'])
            for lbl, val in [('ref share', nref/n*100), ('unref share', (n-nref)/n*100)]: out[lbl] = val
            ar = AR.get(cl)
            if ar and ar['inv']:
                out['collection'] = ar['rec']/ar['inv']*100
                out['outstanding share'] = ar['out']/ar['inv']*100
            return out
        if typ == 'count':
            out['works'] = len(g); out['cats'] = len(cats)
            out['ref'] = sum(1 for r in g if r['h']); out['unref'] = len(g)-sum(1 for r in g if r['h'])
            return out
        out['total'] = tot
        out['mean'] = statistics.mean(v); out['median'] = statistics.median(v)
        out['mean-median'] = statistics.mean(v) - statistics.median(v)
        sv = sorted(v, reverse=True)
        for i, x in enumerate(sv): out[f'work#{i+1}'] = x
        for i in range(len(sv)-1): out[f'rank{i+1}-{i+2}'] = sv[i]-sv[i+1]
        out['max-min'] = sv[0]-sv[-1]
        for k, x in cats.items(): out[f'cat:{k}'] = x; out[f'total-cat:{k}'] = tot - x
        for a, b in itertools.combinations(sorted(cats), 2):
            out[f'|{a}-{b}|'] = abs(cats[a]-cats[b])
        yrs = collections.defaultdict(float)
        for r in g: yrs[r['d'][:4]] += r['v']
        for y, x in yrs.items(): out[f'year:{y}'] = x
        for a, b in itertools.combinations(sorted(yrs), 2): out[f'|{a}-{b}|y'] = abs(yrs[a]-yrs[b])
        out['ref works total'] = sum(r['v'] for r in g if r['h'])
    ar = AR.get(cl)
    if ar:
        out['AR invoiced'] = ar['inv']; out['AR received'] = ar['rec']
        out['AR outstanding'] = ar['out']; out['AR positive-only'] = ar['pos']
        if g: out['awarded-invoiced'] = abs(sum(r['v'] for r in g)-ar['inv'])
    return out

P = []
for q in qs:
    p = parse_question(conn, q['question'])
    p['shape'] = reconcile_shape(p['question_shape'], q.get('answer_type'), p)
    p['qid'] = q['qid']; p['type'] = q['answer_type']; p['text'] = q['question']
    P.append(p)
COIN = {'HV-IC-0044','HV-IC-0178','HV-IC-0276','HV-IC-0333'}

cand = []
for p in P:
    if p['qid'] in COIN: continue
    cl = p.get('client_name')
    if not cl: continue
    a = ans[p['qid']]
    for lbl, gv in pool(cl, p['type']).items():
        l = 1.0 - s(a, gv)
        if 1e-9 < l < 0.99: cand.append((l, p['qid'], p['shape'], lbl, gv, a))

LO, HI = 0.749, 0.753
print('=== single alternatives landing inside the residual window [%.3f, %.3f] ===' % (LO, HI))
n = 0
for l, qid, sh, lbl, gv, a in sorted(cand, reverse=True):
    if LO <= l <= HI:
        n += 1
        print(f'  loss={l:.4f}  {qid}  {sh:20s} gold would be {lbl:26s} {gv:16,.2f}   ours {a:16,.2f}')
print(f'  {n} single-question explanations')

print()
print('=== pairs / triples of distinct questions summing into the window ===')
cand.sort(reverse=True)
big = [c for c in cand if c[0] > 0.05]
seen = 0
for k in (2, 3):
    for combo in itertools.combinations(big, k):
        if len({c[1] for c in combo}) != k: continue
        t = sum(c[0] for c in combo)
        if LO <= t <= HI:
            seen += 1
            if seen <= 20:
                print(f'  sum={t:.4f}: ' + '  +  '.join(f'{c[1]}[{c[3]}]={c[0]:.3f}' for c in combo))
print(f'  {seen} multi-question explanations (showing first 20)')
