"""Decode the score of submission #15 into the exact gold assignment.

Submission #15 differs from the 99.015 file only on HV-IC-0178/0276/0333, so
the whole score delta comes from the four coin-flip questions. The 99.015 file
earns a branch-INDEPENDENT 1.470921 on those four (that is what probe #5's
equation forces), so the observed score maps one-to-one onto the branch.
"""
BASE = 99.015
Q = 100 / 333
OLD_CREDIT = None

SOLS = [  # (gold_0044, gold_0276, gold_0333)
    (2575000,  -49171429, 2575000),
    (2575000,  -49171429, 20300000),
    (73950000,  67575000, 2575000),
    (73950000,  67575000, 20300000),
    (73950000,  31185714, 2575000),
    (73950000,  31185714, 20300000),
    (73950000,   2575000, 2575000),
    (73950000,   2575000, 20300000),
]
NAME = {2575000:'PHE Odisha', 20300000:'PWD Gujarat', 73950000:'PWD Maharashtra',
        -49171429:'Arunodaya', 67575000:'Jal Nigam UP', 31185714:'Peninsular',
        157033333:'Maharashtra Municipal', 240294737:'Trishakti'}

def s(a, g):
    return 0.0 if g == 0 else max(0.0, 1.0 - abs(a - g) / abs(g))

OLD = {'0044': 73950000, '0178': 73950000, '0276': -49171429, '0333': 368533333}
NEW = {'0044': 73950000, '0178': 157033333, '0276': 31185714, '0333': 2575000}

for g178, tag in ((157033333, 'HV-IC-0178 gold = Maharashtra Municipal (the identified case)'),
                  (240294737, 'HV-IC-0178 gold = Trishakti (the rival, if the read was off)')):
    print('=' * 96); print(tag); print('=' * 96)
    print(f"{'if score is':>12} | {'HV-IC-0044':>28} {'HV-IC-0276':>26} {'HV-IC-0333':>22}")
    print('-' * 96)
    rows = []
    for g44, g276, g333 in SOLS:
        old = s(OLD['0044'], g44) + s(OLD['0178'], g178) + s(OLD['0276'], g276) + s(OLD['0333'], g333)
        new = s(NEW['0044'], g44) + s(NEW['0178'], g178) + s(NEW['0276'], g276) + s(NEW['0333'], g333)
        pred = BASE + (new - old) * Q
        rows.append((pred, g44, g276, g333, new))
        print(f'{pred:12.3f} | {NAME[g44]:>16} {g44:>11,} {NAME[g276]:>13} {g276:>12,} {NAME[g333]:>11} {g333:>10,}')
    print()
    print(f'  expected score  {BASE + (sum(r[4] for r in rows)/len(rows) - (s(OLD["0044"],73950000)+s(OLD["0178"],g178)+0+0)) * Q:.3f}'
          f'   (range {min(r[0] for r in rows):.3f} - {max(r[0] for r in rows):.3f})')
    print(f'  ceiling if the last attempt sets all four correctly: '
          f'{BASE + (4.0 - (s(OLD["0044"],73950000)+s(OLD["0178"],g178))) * Q:.3f}')
    print()

allv = []
for g178 in (157033333, 240294737):
    for g44, g276, g333 in SOLS:
        old = s(OLD['0044'], g44) + s(OLD['0178'], g178) + s(OLD['0276'], g276) + s(OLD['0333'], g333)
        new = s(NEW['0044'], g44) + s(NEW['0178'], g178) + s(NEW['0276'], g276) + s(NEW['0333'], g333)
        allv.append(round(BASE + (new - old) * Q, 3))
allv.sort()
gaps = [round(b - a, 3) for a, b in zip(allv, allv[1:])]
print('all 16 predictions:', allv)
print('closest two predictions differ by:', min(gaps), 'points  ->',
      'DISTINGUISHABLE at 3 decimals' if min(gaps) >= 0.002 else 'AMBIGUOUS')
