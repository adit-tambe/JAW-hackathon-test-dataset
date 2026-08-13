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
    out = []
    for q in questions:
        qid = q.get("qid") or q.get("question_id") or q.get("id")
        text = q.get("question") or q.get("text") or ""
        if not qid:
            continue
        out.append({"qid": str(qid), "question": text,
                    "answer_type": (q.get("answer_type") or "money").lower(),
                    "tier": q.get("tier", "")})
    return out


def format_answer(value, answer_type: str) -> str:
    """Plain numbers only: no units, no separators, no symbols."""
    if value is None:
        return "0"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0"
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
        ingest(docs_root, EXTRACTED_DIR)

        banner("2/4  Build — load records into SQLite and reconcile")
        from src.build_db import build_database, validate_database
        conn = build_database()
        validate_database(conn)
        conn.close()
    else:
        print("\n  [skipped] ingest and build — reusing existing database")

    banner("3/4  Answer")
    import sqlite3
    from src.answer_engine import answer_question

    conn = sqlite3.connect(DB_PATH)
    answers: dict[str, str] = {}
    failures = 0
    for i, q in enumerate(questions, 1):
        try:
            raw = answer_question(conn, q["question"], q["qid"], q["answer_type"])
        except Exception as exc:
            raw, failures = None, failures + 1
            print(f"    !! {q['qid']}: {type(exc).__name__}: {exc}")
        answers[q["qid"]] = format_answer(raw, q["answer_type"])
        if i % 50 == 0 or i == len(questions):
            print(f"    {i}/{len(questions)} answered", flush=True)
    conn.close()
    if failures:
        print(f"  {failures} question(s) fell back to a default answer")

    banner("4/4  Write")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["question_id", "answer"])
        for q in questions:
            writer.writerow([q["qid"], answers.get(q["qid"], "0")])
    print(f"  wrote {len(questions)} rows to {out_path}")
    print(f"\n  DONE in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
