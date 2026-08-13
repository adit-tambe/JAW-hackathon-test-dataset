#!/usr/bin/env python3
"""
score.py — grade a submission CSV against a question file that carries answers.

Uses this round's tolerance, which is much tighter than the previous one and
has no partial credit:

    money    max(1 rupee, 0.5% of the correct value)
    count    exact integer
    percent  within 0.05
    days     exact integer

    python tests/score.py --questions FILE.json --submission FILE.csv [--verbose]
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def tolerance(answer_type: str, gold: float) -> float:
    if answer_type == "percent":
        return 0.05
    if answer_type in ("count", "days"):
        return 0.0
    return max(1.0, abs(gold) * 0.005)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--submission", required=True)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    payload = json.loads(Path(args.questions).read_text(encoding="utf-8-sig"))
    questions = payload.get("questions", payload) if isinstance(payload, dict) else payload
    questions = [q for q in questions if q.get("answer") is not None]

    got: dict[str, str] = {}
    with open(args.submission, newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if row and row[0].strip().lower() != "question_id":
                got[row[0].strip()] = row[1].strip()

    hits = 0
    by_shape: Counter = Counter()
    miss_by_shape: Counter = Counter()
    for q in questions:
        qid = q.get("qid") or q.get("question_id")
        gold = float(q["answer"])
        answer_type = (q.get("answer_type") or "money").lower()
        shape = q.get("shape", "?")
        try:
            mine = float(got.get(qid, "nan"))
        except ValueError:
            mine = float("nan")
        ok = abs(mine - gold) <= tolerance(answer_type, gold)
        hits += ok
        by_shape[shape] += 1
        if not ok:
            miss_by_shape[shape] += 1
            if args.verbose:
                src = f" (from {q['from']})" if "from" in q else ""
                print(f"  MISS {qid}{src} [{shape}] gold={gold:,} got={mine:,}")
                print(f"       {q['question'][:150]}")

    total = len(questions)
    print(f"\n  {hits}/{total} exact  ({100.0 * hits / max(1, total):.1f}%)")
    if miss_by_shape:
        print("  misses by shape:")
        for shape, n in miss_by_shape.most_common():
            print(f"      {shape:24s} {n}/{by_shape[shape]}")
    return 0 if hits == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
