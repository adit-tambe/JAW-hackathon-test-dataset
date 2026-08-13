"""For each alternative convention A: if A were the gold rule, how much credit
would our current answers be losing? The residual is 0.751 questions, so an
alternative that costs ~0.751 is a candidate explanation."""
import json, csv, sqlite3, sys, statistics, datetime, collections, openpyxl
sys.path.insert(0, '.')
from src.answer_engine import parse_question, reconcile_shape
from src.config import DB_PATH

qs = json.load(open('validation_questions.json', encoding='utf-8'))['questions']
ans = {r[0].strip(): float(r[1]) for r in csv.reader(open('attempt_final.csv', newline='', encoding='utf-8-sig'))
       if r and r[0].strip().lower() != 'question_id'}
conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
W = collections.defaultdict(list)
for r in conn.execute('''select cl.client_name c, w.contract_value v, w.work_category k, w.completion_date d,
                                w.commencement_date cd, w.has_reference_letter h
                         from works w join clients cl on cl.client_id=w.client_id'''):
    W[r['c']].append(dict(r))
wb = openpyxl.load_workbook('documents/workbooks/Receivables_Ageing.xlsx', data_only=True)
AR = collections.defaultdict(lambda: {'inv':0,'rec':0,'out':0})
for row in list(wb['AR Ageing'].iter_rows(values_only=True))[1:]:
    if row and row[0] and row[1]:
        a = AR[row[1].strip()]; a['inv'] += row[3] or 0; a['rec'] += row[5] or 0; a['out'] += row[6] or 0

def s(a, g):
    if g == 0: return 0.0
    return max(0.0, 1.0 - abs(a - g) / abs(g))

P = []
for q in qs:
    p = parse_question(conn, q['question'])
    p['shape'] = reconcile_shape(p['question_shape'], q.get('answer_type'), p)
    p['qid'] = q['qid']; p['type'] = q['answer_type']
    P.append(p)

def loss(alt_fn, shapes):
    """total credit lost across questions of `shapes` if alt_fn were gold"""
    tot = 0.0; n = 0
    for p in P:
        if p['shape'] not in shapes: continue
        g = alt_fn(p)
        if g is None: continue
        n += 1; tot += 1.0 - s(ans[p['qid']], g)
    return tot, n

ALTS = []
def alt(name, shapes):
    def deco(f):
        ALTS.append((name, shapes, f)); return f
    return deco

@alt('threshold_aggregate: strictly > instead of >=', {'threshold_aggregate'})
def _(p):
    cl = p.get('client_name'); t = p.get('threshold_value') or p.get('target_value')
    if not cl or not t or cl not in W: return None
    return sum(r['v'] for r in W[cl] if r['v'] > t)

@alt('percent answers at full precision (we round to 2dp)', {'collection_percent'})
def _(p):
    cl = p.get('client_name')
    if not cl or cl not in AR: return None
    return AR[cl]['rec'] / AR[cl]['inv'] * 100

@alt('referenced_share at full precision', {'referenced_share'})
def _(p):
    cl = p.get('client_name')
    if not cl or cl not in W: return None
    g = W[cl]; return sum(1 for r in g if r['h']) / len(g) * 100

@alt('avg_work_size unrounded', {'avg_work_size'})
def _(p):
    cl = p.get('client_name')
    if not cl or cl not in W: return None
    return statistics.mean([r['v'] for r in W[cl]])

@alt('mean_median_diff unrounded', {'mean_median_diff'})
def _(p):
    cl = p.get('client_name')
    if not cl or cl not in W: return None
    v = [r['v'] for r in W[cl]]; return statistics.mean(v) - statistics.median(v)

@alt('date_span inclusive (+1 day)', {'date_span'})
def _(p):
    return ans[p['qid']] + 1

@alt('yearly_diff keyed on commencement year, not completion', {'yearly_diff'})
def _(p):
    cl = p.get('client_name'); y1, y2 = p.get('year1'), p.get('year2')
    if not cl or not y1 or not y2 or cl not in W: return None
    a = sum(r['v'] for r in W[cl] if (r['cd'] or '')[:4] == str(y1))
    b = sum(r['v'] for r in W[cl] if (r['cd'] or '')[:4] == str(y2))
    return abs(a - b)

@alt('rank_value = largest minus smallest', {'rank_value'})
def _(p):
    cl = p.get('client_name')
    if not cl or cl not in W: return None
    v = sorted(r['v'] for r in W[cl]); return v[-1] - v[0] if len(v) > 1 else None

@alt('unbilled_gap signed (awarded - invoiced, not abs)', {'unbilled_gap'})
def _(p):
    cl = p.get('client_name')
    if not cl or cl not in W or cl not in AR: return None
    return sum(r['v'] for r in W[cl]) - AR[cl]['inv']

print(f"{'alternative convention':58s} {'n':>4} {'credit lost if it were gold':>28}")
print('-' * 92)
for name, shapes, f in ALTS:
    tot, n = loss(f, shapes)
    flag = '   <<< MATCHES THE 0.751 RESIDUAL' if 0.70 <= tot <= 0.80 else ''
    print(f'{name:58s} {n:4d} {tot:14.4f} questions{flag}')
print()
print('target residual = 0.751 questions')
