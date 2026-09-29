#!/usr/bin/env python3
"""
evaluate.py — score a submission against the hidden question set.

Answers are numbers, so scoring is mechanical and a near-miss still earns credit. Two regimes,
because "within 2%" means something very different for a contract value than for a count of four
projects:

    MONEY / large values (|gold| >= 100)
        relative error <= 0.5%   ->  1.0     you computed it
                       <= 2%     ->  0.7     right method, a value slightly off
                       <= 10%    ->  0.3     right shape, missed a contributor
                       otherwise ->  0.0

    COUNTS / small values (|gold| < 100)
        exact                    ->  1.0
        off by one               ->  0.3     one document missed or double-counted
        otherwise                ->  0.0

Percentages are graded as counts when the gold is under 100, which is every percentage — so 66.67
must be answered to within a point, not within 2% of itself.

Submission format — one JSON object per line:
    {"qid": "HV-IC-0001", "answer": 1069600000}

Usage:
    python evaluate.py --submission my_answers.jsonl --questions hidden_questions.json
    python evaluate.py --self-test
"""
import argparse, collections, json, sys


def score_one(gold, got):
    """Score a single answer. Returns 0.0 if the answer is missing or unparseable."""
    if got is None:
        return 0.0
    try:
        gold, got = float(gold), float(got)
    except (TypeError, ValueError):
        return 0.0
    if abs(gold) < 100:                               # counts and percentages
        if got == gold:
            return 1.0
        return 0.3 if abs(got - gold) <= 1 else 0.0
    err = abs(got - gold) / abs(gold)
    if err <= 0.005:
        return 1.0
    if err <= 0.02:
        return 0.7
    if err <= 0.10:
        return 0.3
    return 0.0


def evaluate(questions, submission):
    got = {}
    for line in submission:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            got[rec["qid"]] = rec.get("answer")
        except (json.JSONDecodeError, KeyError):
            continue

    rows, by_shape = [], collections.defaultdict(lambda: [0.0, 0])
    for q in questions:
        gold = q.get("answer", q.get("answer_gold"))
        s = score_one(gold, got.get(q["qid"]))
        rows.append({"qid": q["qid"], "shape": q.get("shape"), "gold": gold,
                     "answered": got.get(q["qid"]), "score": s})
        by_shape[q.get("shape", "?")][0] += s
        by_shape[q.get("shape", "?")][1] += 1

    total = sum(r["score"] for r in rows)
    return rows, by_shape, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission")
    ap.add_argument("--questions", default="sample_questions.json")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--per-question", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        cases = [
            # (gold, answered, expected) — the bands, asserted rather than described
            (1_000_000_000, 1_000_000_000, 1.0),      # exact
            (1_000_000_000, 1_004_000_000, 1.0),      # 0.4%  -> full
            (1_000_000_000, 1_015_000_000, 0.7),      # 1.5%  -> most
            (1_000_000_000, 1_080_000_000, 0.3),      # 8%    -> partial
            (1_000_000_000, 1_500_000_000, 0.0),      # 50%   -> none
            (5, 5, 1.0),                              # exact count
            (5, 6, 0.3),                              # off by one
            (5, 8, 0.0),                              # wrong
            (66.67, 66.67, 1.0),                      # percentage, exact
            (66.67, 67.0, 0.3),                       # percentage, within a point
            (66.67, 0.6667, 0.0),                     # fraction instead of percent
            (100, 100, 1.0),
            (1_000_000_000, None, 0.0),               # unanswered
            (1_000_000_000, "not a number", 0.0),
        ]
        bad = [(g, a_, e, score_one(g, a_)) for g, a_, e in cases if score_one(g, a_) != e]
        for g, a_, e, s in bad:
            print(f"  FAIL gold={g} answered={a_} expected={e} got={s}")
        print(f"self-test: {len(cases)-len(bad)}/{len(cases)} bands correct")
        return sys.exit(1 if bad else 0)

    questions = json.loads(open(a.questions).read())["questions"]
    if not a.submission:
        sys.exit("--submission is required (or use --self-test)")
    rows, by_shape, total = evaluate(questions, open(a.submission))

    if a.per_question:
        for r in rows:
            mark = "OK " if r["score"] == 1.0 else f"{r['score']:.1f}"
            print(f"  {mark}  {r['qid']:12s} gold={r['gold']}  answered={r['answered']}")
        print()
    print(f"{'shape':26s} {'score':>8s}  {'n':>3s}")
    for shape, (s, n) in sorted(by_shape.items(), key=lambda kv: -kv[1][0] / max(kv[1][1], 1)):
        print(f"{shape:26s} {s:8.1f}  {n:3d}   {s/max(n,1):.0%}")
    print(f"\nTOTAL {total:.1f} / {len(rows)}  =  {total/max(len(rows),1):.1%}")


if __name__ == "__main__":
    main()
