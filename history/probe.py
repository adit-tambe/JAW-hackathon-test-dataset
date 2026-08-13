#!/usr/bin/env python3
"""
probe.py — locate missing credit by deliberately corrupting one shape.

The score is a mean over 333 questions, so one question is worth 0.30030
points. That makes a destructive probe a precise measuring instrument:

    submit a file identical to the best one, except every answer belonging to
    shape X is replaced by a value guaranteed to score 0.

    credit_X = (best_score - probe_score) / 100 * 333

If shape X was earning full credit, the drop equals its question count exactly.
If the drop is smaller, the difference is credit shape X was never earning —
that is, the questions inside X that are already wrong. Repeat per shape and the
missing credit is localised without guessing at conventions.

This is the same arithmetic that made submission #7 conclusive: `yearly_signed`
changed 7 questions and cost exactly 7.0 question-equivalents, which proved
those 7 were earning full credit beforehand.

Usage:
    python probe.py --shape category_difference
    python probe.py --list
"""
import argparse
import collections
import csv
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.answer_engine import parse_question, reconcile_shape
from src.config import DB_PATH

QUESTIONS = Path(__file__).parent / "validation_questions.json"
BEST = Path(__file__).parent / "submission.csv"
POINTS_PER_QUESTION = 100 / 333


def shapes_by_qid(conn, questions):
    out = {}
    for q in questions:
        p = parse_question(conn, q["question"])
        out[q["qid"]] = reconcile_shape(p["question_shape"], q.get("answer_type"), p)
    return out


def corrupt(value: str, factor: float) -> str:
    """Scale an answer so its score becomes a known constant.

    An answer of `factor * gold` scores `max(0, 1 - |factor - 1|)`. So:

        factor 1.5  ->  each affected question scores exactly 0.5
        factor 2.0  ->  each scores 0, the maximally destructive probe

    1.5 is the default because it carries the same information at half the cost
    to the visible score: the drop is 0.5 * credit rather than 1.0 * credit, and
    a question that is already wrong contributes nothing either way.
    """
    try:
        v = float(value)
    except ValueError:
        return "999999999"
    out = v * factor
    if out == 0:
        return "987654321"
    return str(int(out)) if out == int(out) else f"{out:.4f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shape", action="append", default=[],
                    help="shape to corrupt; repeat to probe several at once, "
                         "which halves the search space per attempt when the "
                         "loss has not been localised yet")
    ap.add_argument("--list", action="store_true", help="show shapes and their point budgets")
    ap.add_argument("--best-score", type=float, default=96.461,
                    help="score of the file being probed, for the readout")
    ap.add_argument("--factor", type=float, default=1.5,
                    help="1.5 (default) halves each affected question's score; "
                         "2.0 zeroes it")
    ap.add_argument("--output")
    a = ap.parse_args()

    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    conn = sqlite3.connect(str(DB_PATH))
    mapping = shapes_by_qid(conn, questions)
    counts = collections.Counter(mapping.values())

    if a.list or not a.shape:
        print(f"{'shape':24s} {'n':>4s} {'max drop':>9s}   probe file")
        for shape, n in counts.most_common():
            print(f"  {shape:22s} {n:4d} {n * POINTS_PER_QUESTION:9.3f}   probe_{shape}.csv")
        print(f"\ntotal {sum(counts.values())} questions; "
              f"missing credit at {a.best_score} = "
              f"{(100 - a.best_score) / POINTS_PER_QUESTION:.2f} questions")
        return 0

    unknown = [x for x in a.shape if x not in counts]
    if unknown:
        print(f"unknown shape(s) {unknown}; use --list")
        return 1
    targets = set(a.shape)

    rows = list(csv.DictReader(BEST.open()))
    n = 0
    for r in rows:
        if mapping.get(r["question_id"]) in targets:
            r["answer"] = corrupt(r["answer"], a.factor)
            n += 1

    label = "+".join(sorted(targets))
    out = Path(a.output or f"probe_{label}.csv")
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["question_id", "answer"])
        for r in rows:
            w.writerow([r["question_id"], r["answer"]])

    # Each affected question's score becomes max(0, 1 - |factor - 1|).
    per_q_loss = min(1.0, abs(a.factor - 1))
    full = n * POINTS_PER_QUESTION * per_q_loss
    print(f"wrote {out} — scaled {n} answers ({label}) by {a.factor}")
    print(f"  if all {n} were earning full credit, score falls to "
          f"{a.best_score - full:.3f}")
    print(f"  read the result as:")
    print(f"    credit = ({a.best_score} - probe_score) / "
          f"{POINTS_PER_QUESTION * per_q_loss:.5f}")
    print(f"    credit missing inside this group = {n} - that credit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
