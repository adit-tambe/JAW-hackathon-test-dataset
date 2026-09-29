#!/usr/bin/env python3
"""
main.py — the whole pipeline, from a directory of documents to a CSV of answers.

    python main.py --docs DIR --questions FILE.json --out submission.csv

Stages:
    1. ingest    walk --docs, type every file by its contents, run the typed
                 extractor for each, write JSON records
    2. build     load those records into SQLite and reconcile them against the
                 credentials pack
    3. answer    resolve every question against the database
    4. write     one row per question, header included

Nothing here reads a checked-in manifest or assumes a directory layout.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def banner(text: str) -> None:
    print()
    print("=" * 66)
    print(f"  {text}")
    print("=" * 66, flush=True)


def load_questions(path: Path) -> list[dict]:
    """Accept either a bare list or an object with a 'questions' key."""
    with open(path, encoding="utf-8-sig") as fh:
        payload = json.load(fh)
    questions = payload.get("questions", payload) if isinstance(payload, dict) else payload
    out, seen = [], set()
    for q in questions:
        qid = q.get("qid") or q.get("question_id") or q.get("id")
        text = q.get("question") or q.get("text") or ""
        if not qid:
            continue
        qid = str(qid).strip()
        if qid in seen:
            # One row per question. A repeated id would otherwise produce two
            # rows for the same question, which is not a submission shape they
            # asked for.
            continue
        seen.add(qid)
        out.append({"qid": qid, "question": text,
                    "answer_type": (q.get("answer_type") or "money").strip().lower(),
                    "tier": q.get("tier", "")})
    return out


def write_csv(out_path: Path, questions: list[dict], answers: dict) -> None:
    """Write the submission atomically.

    Via a temporary file and a replace, so a run interrupted mid-write cannot
    leave a half-written CSV where a complete one used to be.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["question_id", "answer"])
        for q in questions:
            writer.writerow([q["qid"], answers.get(q["qid"], "0")])
    os.replace(tmp, out_path)


def format_answer(value, answer_type: str) -> str:
    """Plain numbers only: no units, no separators, no symbols.

    Never raises. This is called outside the guard that wraps answering, so an
    exception here would take down the whole pass rather than one question —
    and nan or inf is exactly what a degenerate division produces, which is the
    kind of value a new estate can hand us without warning.
    """
    if value is None:
        return "0"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0"
    if value != value or value in (float("inf"), float("-inf")):
        return "0"                      # nan / inf: no answer, not a crash
    if answer_type in ("count", "days"):
        return str(int(round(value)))
    if answer_type == "percent":
        return f"{round(value + 0.0, 2):g}"
    # money and anything else: integer rupees
    return str(int(round(value)))


def main() -> int:
    ap = argparse.ArgumentParser(description="Document estate -> answers")
    ap.add_argument("--docs", required=True, help="root directory of documents")
    ap.add_argument("--questions", required=True, help="questions JSON")
    ap.add_argument("--out", required=True, help="output CSV path")
    ap.add_argument("--work-dir", default=os.getenv("JAW_DATA_DIR"),
                    help="scratch directory (default: ./data)")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip the LLM layer; deterministic engine only")
    ap.add_argument("--samples", type=int, default=3,
                    help="LLM votes on a disagreement (default 3)")
    ap.add_argument("--workers", type=int, default=None,
                    help="questions answered concurrently (default: LLM_CONCURRENCY)")
    ap.add_argument("--time-budget", type=float,
                    default=float(os.getenv("JAW_TIME_BUDGET", "5400")),
                    help="seconds before the model pass gives way to the engine "
                         "(0 disables the deadline)")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="reuse an existing database (development only)")
    args = ap.parse_args()

    started = time.time()
    docs_root = Path(args.docs).expanduser().resolve()
    questions_path = Path(args.questions).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()

    # Must be set before anything imports src.config.
    os.environ["JAW_DOCS_DIR"] = str(docs_root)
    if args.work_dir:
        os.environ["JAW_DATA_DIR"] = str(Path(args.work_dir).expanduser().resolve())

    from src.config import DB_PATH, EXTRACTED_DIR

    print(f"  docs      {docs_root}")
    print(f"  questions {questions_path}")
    print(f"  out       {out_path}")
    print(f"  work dir  {EXTRACTED_DIR.parent}")

    questions = load_questions(questions_path)
    print(f"  {len(questions)} questions loaded", flush=True)

    if not args.skip_ingest:
        banner("1/4  Ingest — walk the document tree and type by content")
        from src.ingest import ingest
        ingest(docs_root, EXTRACTED_DIR, use_llm=not args.no_llm)

        banner("2/4  Build — load records into SQLite and reconcile")
        from src.build_db import build_database, validate_database
        conn = build_database()
        validate_database(conn)
        conn.close()
    else:
        print("\n  [skipped] ingest and build — reusing existing database")

    banner("3/4  Answer — deterministic pass")
    import collections
    import sqlite3
    from src.llm import map_concurrent, stats as llm_stats
    from src.resolve import deterministic, resolve
    from src.schema_card import build_card

    conn = sqlite3.connect(DB_PATH)
    card = build_card(conn)
    print(f"  schema card: {len(card)} chars (~{len(card)//4} tokens)")

    # The deterministic pass runs first, on its own, and its result is written
    # out immediately. It takes about a second, needs no network, and from that
    # moment there is a complete, valid submission on disk. Everything after
    # this can only improve it — and if anything later hangs, is killed, or
    # throws, the file already there is the one that gets graded.
    answers: dict[str, str] = {}
    for q in questions:
        try:
            first = deterministic(conn, q["question"], q["qid"], q["answer_type"])
            value = first["value"]
        except Exception:
            value = None
        answers[q["qid"]] = format_answer(value, q["answer_type"])
    write_csv(out_path, questions, answers)
    print(f"  baseline submission written ({len(answers)} rows) — safe from here")

    use_llm = False
    if not args.no_llm:
        from src.llm import available, endpoint
        print(f"  probing {endpoint()} ...", flush=True)
        use_llm = available()
        print(f"  LLM {'available' if use_llm else 'UNAVAILABLE — keeping the deterministic pass'}")

    routes: collections.Counter = collections.Counter()
    disagreements: list[tuple] = []
    done = [0]
    deadline = started + args.time_budget if args.time_budget > 0 else None
    degraded = [False]

    def work(q: dict):
        # One connection per worker: sqlite3 objects are not shareable.
        local = sqlite3.connect(DB_PATH)
        # Past the deadline the remaining questions fall back to the engine, so
        # the run always finishes rather than being cut off part way.
        over = deadline is not None and time.time() > deadline
        if over and not degraded[0]:
            degraded[0] = True
            print(f"    !! time budget reached — remaining questions answered "
                  f"by the engine alone", flush=True)
        try:
            outcome = resolve(local, DB_PATH, card, q["question"], q["qid"],
                              q["answer_type"], use_llm=use_llm and not over,
                              samples=args.samples)
        except Exception as exc:
            print(f"    !! {q['qid']}: {type(exc).__name__}: {exc}")
            outcome = {"value": None, "route": "error", "shape": "?",
                       "engine": {}, "llm": None}
        finally:
            local.close()
        done[0] += 1
        if done[0] % 25 == 0 or done[0] == len(questions):
            print(f"    {done[0]}/{len(questions)} answered", flush=True)
        return q["qid"], outcome, q["answer_type"]

    if use_llm:
        banner("3b/4  Answer — model cross-check")
        try:
            results = map_concurrent(work, questions, workers=args.workers)
        except Exception as exc:
            print(f"  !! cross-check pass failed ({type(exc).__name__}: {exc})")
            print("     keeping the deterministic submission already written")
            results = []
        for qid, outcome, answer_type in results:
            # Never let the refinement pass replace a real answer with nothing.
            if outcome["value"] is not None:
                answers[qid] = format_answer(outcome["value"], answer_type)
            routes[outcome["route"]] += 1
            if outcome["route"] not in ("agreed", "engine", "engine-only"):
                disagreements.append((qid, outcome))
    else:
        routes["engine-only"] = len(questions)
    conn.close()

    print("\n  how each answer was reached:")
    for route, n in routes.most_common():
        print(f"      {route:32s} {n}")
    if use_llm:
        print(f"  llm calls: {llm_stats()}")
    if disagreements:
        print(f"\n  {len(disagreements)} question(s) where the two systems parted:")
        for qid, outcome in disagreements[:25]:
            engine_value = (outcome.get("engine") or {}).get("value")
            llm_value = (outcome.get("llm") or {}).get("value")
            print(f"      {qid}  {outcome['shape']:22s} engine={engine_value} "
                  f"llm={llm_value} -> {outcome['route']}")

    banner("4/4  Write")
    write_csv(out_path, questions, answers)
    print(f"  wrote {len(questions)} rows to {out_path}")
    print(f"\n  DONE in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
