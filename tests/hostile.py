#!/usr/bin/env python3
"""
hostile.py — try to make the pipeline fail to produce a submission.

Correctness is worth nothing if the run dies. These are the inputs most likely
to differ from our sample estate in ways that crash a parser or an assumption:
a corrupt file, a document family we have never seen, an empty directory, a
questions file shaped slightly differently from ours.

The bar for every case is the same and is deliberately low: **a valid CSV with
one row per question must exist afterwards**. Wrong answers are survivable.
A missing file is not.

    python tests/hostile.py --docs documents
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(docs: Path, questions: Path, out: Path, work: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "main.py"),
         "--docs", str(docs), "--questions", str(questions),
         "--out", str(out), "--work-dir", str(work), "--no-llm"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=900)
    return proc.returncode, (proc.stdout + proc.stderr)


def check_csv(out: Path, expected_ids: list[str]) -> str | None:
    if not out.exists():
        return "no CSV was written"
    with open(out, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.reader(fh) if r]
    if not rows or rows[0][0].strip().lower() != "question_id":
        return "missing or malformed header"
    got = {r[0].strip() for r in rows[1:]}
    missing = [q for q in expected_ids if q not in got]
    if missing:
        return f"{len(missing)} question(s) have no row, e.g. {missing[:3]}"
    for row in rows[1:]:
        if len(row) != 2:
            return f"malformed row: {row!r}"
        try:
            float(row[1])
        except ValueError:
            return f"non-numeric answer for {row[0]}: {row[1]!r}"
    return None


QUESTIONS_OK = {"questions": [
    {"qid": "H-001", "question": "What is the total completed contract value for "
                                 "Trishakti Power Generation Corporation?",
     "answer_type": "money"},
    {"qid": "H-002", "question": "How many distinct work categories has Pooja Sen "
                                 "completed?", "answer_type": "count"},
]}

# Shaped differently on purpose: a bare list, alternative id and text keys, a
# missing answer_type, an id with awkward characters, and a duplicate.
QUESTIONS_ODD = [
    {"question_id": "H-101", "text": "Total value of works for Jal Nigam, Jharkhand?",
     "answer_type": "MONEY"},
    {"id": "H-102", "question": "How many works lack a reference letter for "
                                "Public Health Engineering Dept, Odisha?"},
    {"qid": "H-103 ", "question": "  ", "answer_type": "percent"},
    {"qid": "H-104", "question": "Average contract value for a client that does "
                                 "not exist in these records at all?",
     "answer_type": "money"},
    {"qid": "H-104", "question": "duplicate id on purpose", "answer_type": "count"},
]


def scenario_corrupt(src_docs: Path, dest: Path) -> None:
    """A handful of real documents, plus files designed to break a parser."""
    dest.mkdir(parents=True, exist_ok=True)
    real = sorted(src_docs.rglob("*.pdf"))[:40] + sorted(src_docs.rglob("*.xlsx"))[:3]
    for i, path in enumerate(real):
        target = dest / f"nested/{i % 3}/deep" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    (dest / "broken.pdf").write_bytes(b"%PDF-1.4\nthis is not a pdf at all\n%%EOF")
    (dest / "empty.pdf").write_bytes(b"")
    (dest / "sheet.xlsx").write_bytes(b"PK\x03\x04 not really a workbook")
    (dest / "notes.txt").write_text("a file type we do not read at all")
    # A well-formed PDF of a document family that does not exist in our rules.
    try:
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "MARINE INSURANCE COVER NOTE\nPolicy 88213\n"
                                   "Insured sum INR 4,20,00,000")
        doc.save(dest / "unknown_family.pdf")
        doc.close()
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default=str(ROOT / "documents"))
    args = ap.parse_args()
    src_docs = Path(args.docs).resolve()

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        cases: list[tuple[str, Path, object]] = []

        empty = tmp / "empty_docs"; empty.mkdir()
        cases.append(("empty document directory", empty, QUESTIONS_OK))

        hostile = tmp / "hostile_docs"
        scenario_corrupt(src_docs, hostile)
        cases.append(("corrupt files, unknown family, odd nesting", hostile, QUESTIONS_OK))
        cases.append(("oddly shaped questions file", hostile, QUESTIONS_ODD))
        cases.append(("empty questions list", hostile, {"questions": []}))
        cases.append(("full estate, questions as a bare list", src_docs,
                      QUESTIONS_OK["questions"]))

        for i, (name, docs, questions) in enumerate(cases):
            qpath = tmp / f"q{i}.json"
            qpath.write_text(json.dumps(questions), encoding="utf-8")
            out = tmp / f"out{i}.csv"
            work = tmp / f"work{i}"
            print(f"\n=== {name} ===", flush=True)
            try:
                code, output = run(docs, qpath, out, work)
            except subprocess.TimeoutExpired:
                failures.append((name, "timed out"))
                print("  FAIL timed out")
                continue

            raw = questions.get("questions", []) if isinstance(questions, dict) else questions
            expected = []
            for q in raw:
                qid = (q.get("qid") or q.get("question_id") or q.get("id") or "").strip()
                if qid and qid not in expected:
                    expected.append(qid)

            problem = check_csv(out, expected)
            if code != 0:
                problem = problem or f"exit code {code}"
            if problem:
                failures.append((name, problem))
                print(f"  FAIL {problem}")
                print("  --- last output ---")
                print("\n".join(output.strip().splitlines()[-15:]))
            else:
                print(f"  ok  exit 0, {len(expected)} row(s), all numeric")

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} scenario(s) failed:")
        for name, problem in failures:
            print(f"  - {name}: {problem}")
        return 1
    print(f"all {len(cases)} hostile scenarios produced a valid submission")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
