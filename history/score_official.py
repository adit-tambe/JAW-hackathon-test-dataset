#!/usr/bin/env python3
"""
score_official.py — score a submission under the metric the organisers publish.

The bundled evaluate.py grades in bands (1.0 / 0.7 / 0.3 / 0) and treats any
gold under 100 as a count. The hackathon page states a different, continuous
formula:

    Score = max(0, 1 - |Your Answer - Correct Answer| / Correct Answer)

Two consequences worth designing for, both of which this script makes visible:

  * There is no free band. Being 0.4% out costs 0.4% of the mark instead of
    nothing, so precision pays continuously.
  * An unanswered or zero answer scores zero, while an answer of roughly the
    right size keeps most of the mark. Guessing beats abstaining.

Usage:
    python score_official.py --submission submission.csv
    python score_official.py --submission submission.jsonl --per-question
"""
import argparse
import collections
import csv
import json
import sys


def score_one(gold, got):
    """The published formula. Missing or unparseable answers score zero."""
    if got is None:
        return 0.0
    try:
        gold, got = float(gold), float(got)
    except (TypeError, ValueError):
        return 0.0
    if gold == 0:
        return 1.0 if got == 0 else 0.0
    return max(0.0, 1.0 - abs(got - gold) / abs(gold))


def load_submission(path):
    """Accept either the CSV (question_id,answer) or the JSONL form."""
    answers = {}
    if str(path).lower().endswith('.csv'):
        with open(path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                qid = row.get('question_id') or row.get('qid')
                if qid:
                    answers[qid.strip()] = row.get('answer')
    else:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    answers[rec['qid']] = rec.get('answer')
                except (json.JSONDecodeError, KeyError):
                    continue
    return answers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--submission', required=True)
    ap.add_argument('--questions', default='sample_questions.json')
    ap.add_argument('--per-question', action='store_true')
    a = ap.parse_args()

    questions = json.loads(open(a.questions, encoding='utf-8').read())['questions']
    got = load_submission(a.submission)

    rows, by_shape = [], collections.defaultdict(lambda: [0.0, 0])
    for q in questions:
        gold = q.get('answer', q.get('answer_gold'))
        s = score_one(gold, got.get(q['qid']))
        rows.append((q['qid'], q.get('shape'), gold, got.get(q['qid']), s))
        by_shape[q.get('shape', '?')][0] += s
        by_shape[q.get('shape', '?')][1] += 1

    if a.per_question:
        for qid, shape, gold, ans, s in rows:
            mark = 'OK ' if s == 1.0 else f'{s:.3f}'
            print(f'  {mark}  {qid:12s} gold={gold}  answered={ans}')
        print()

    print(f"{'shape':26s} {'score':>8s}  {'n':>3s}")
    for shape, (s, n) in sorted(by_shape.items(), key=lambda kv: -kv[1][0] / max(kv[1][1], 1)):
        print(f'{shape:26s} {s:8.3f}  {n:3d}   {s/max(n,1):.1%}')

    total = sum(r[4] for r in rows)
    unanswered = sum(1 for r in rows if r[3] is None)
    print(f'\nTOTAL {total:.3f} / {len(rows)}  =  {total/max(len(rows),1):.2%}')
    if unanswered:
        print(f'unanswered: {unanswered} (each scores 0 — answer everything)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
