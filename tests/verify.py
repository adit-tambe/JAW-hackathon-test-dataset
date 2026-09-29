#!/usr/bin/env python3
"""
verify.py — run every check we have, in one command.

    python tests/verify.py                 # engine only, no endpoint needed
    python tests/verify.py --llm URL       # also exercise the model path

What each check is for:

  scrambled     the estate re-nested with every file renamed to a hash. Proves
                nothing depends on a path or a file name, which is the single
                assumption that would produce a zero.
  regression    the 333 questions we have verified answers for. Guards against
                a change quietly moving an answer that was right.
  samples       the organisers' own questions, graded under this round's
                exact-match tolerance.
  paraphrases   the same questions reworded. Measures what the system is worth
                on wording it has not seen, which is what it will be graded on.
  novel         questions reaching tables no deterministic shape computes over.
                The graded set carries an "exhaustive" tier, so these matter.
  hostile       corrupt files, unknown families, odd question files. The bar is
                only that a valid submission exists afterwards.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path) -> dict:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return {r[0].strip(): r[1].strip() for r in csv.reader(fh)
                if r and r[0].strip().lower() != "question_id"}


def run_pipeline(docs: Path, questions: Path, out: Path, work: Path,
                 llm: str | None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    cmd = [sys.executable, "-X", "utf8", str(ROOT / "main.py"),
           "--docs", str(docs), "--questions", str(questions),
           "--out", str(out), "--work-dir", str(work)]
    if llm:
        env["LLM_BASE_URL"] = llm
    else:
        cmd.append("--no-llm")
    return subprocess.run(cmd, capture_output=True, text=True, env=env,
                          cwd=str(ROOT), timeout=3600)


def score(questions: Path, submission: Path) -> tuple[int, int]:
    payload = json.loads(questions.read_text(encoding="utf-8-sig"))
    items = payload.get("questions", payload) if isinstance(payload, dict) else payload
    items = [q for q in items if q.get("answer") is not None]
    got = load(submission)
    hits = 0
    for q in items:
        qid = q.get("qid") or q.get("question_id")
        gold = float(q["answer"])
        kind = (q.get("answer_type") or "money").lower()
        tol = 0.05 if kind == "percent" else (0.0 if kind in ("count", "days")
                                              else max(1.0, abs(gold) * 0.005))
        try:
            mine = float(got.get(qid, "nan"))
        except ValueError:
            mine = float("nan")
        hits += abs(mine - gold) <= tol
    return hits, len(items)


def scramble(src: Path, dest: Path, seed: int = 20260813) -> None:
    """Copy the estate under fresh names and an unrelated directory layout."""
    import hashlib
    import random
    import shutil
    if dest.exists():
        shutil.rmtree(dest)
    rnd = random.Random(seed)
    buckets = ["intake/a", "intake/b", "archive/2019/q1", "misc",
               "vault/deep/deeper/deepest", "scans"]
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".pdf", ".xlsx"):
            continue
        name = hashlib.md5(f"{seed}:{path}".encode()).hexdigest()[:16] + path.suffix.lower()
        target = dest / rnd.choice(buckets) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def check_permutations(docs: Path, tmp: Path, questions: Path, n: int = 3) -> tuple[str, bool]:
    """The same documents, arranged differently, must give the same answers.

    Discovery order follows the directory walk, so it changes with the layout,
    and anything that resolves an ambiguity by taking the first row it happens
    to see will answer differently on a different arrangement of the very same
    estate. That is invisible in a single run and produces a confident wrong
    number rather than an error, so it is worth an explicit check.
    """
    outputs = []
    for seed in range(1, n + 1):
        source = tmp / f"perm{seed}"
        scramble(docs, source, seed=seed)
        out = tmp / f"perm{seed}.csv"
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "main.py"),
             "--docs", str(source), "--questions", str(questions),
             "--out", str(out), "--work-dir", str(tmp / f"permw{seed}"), "--no-llm"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=1800)
        if proc.returncode != 0 or not out.exists():
            return f"permutation {seed} failed to run", False
        outputs.append(out)
    first = load(outputs[0])
    for other in outputs[1:]:
        moved = [k for k in first if first[k] != load(other).get(k)]
        if moved:
            return f"{len(moved)} answer(s) depend on document order: {moved[:5]}", False
    return f"{n} arrangements of the estate agree exactly", True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default=str(ROOT / "documents"))
    ap.add_argument("--llm", default=None, help="LLM_BASE_URL to exercise the model path")
    ap.add_argument("--skip-scramble", action="store_true")
    args = ap.parse_args()
    docs = Path(args.docs).resolve()

    results: list[tuple[str, str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        source = docs
        if not args.skip_scramble:
            print("=== scrambling the document tree ===", flush=True)
            source = tmp / "scrambled"
            scramble(docs, source)
            print(f"  {sum(1 for _ in source.rglob('*') if _.is_file())} files "
                  f"renamed and re-nested")

        checks = [
            ("regression", ROOT / "validation_questions.json", ROOT / "tests/baseline_answers.csv"),
            ("samples", ROOT / "sample_questions.json", None),
            ("paraphrases", ROOT / "tests/paraphrases.json", None),
            ("novel", ROOT / "tests/novel.json", None),
        ]
        for i, (name, questions, baseline) in enumerate(checks):
            if not questions.exists():
                continue
            out = tmp / f"{name}.csv"
            print(f"\n=== {name} ===", flush=True)
            proc = run_pipeline(source, questions, out, tmp / f"w{i}", args.llm)
            if proc.returncode != 0 or not out.exists():
                print("\n".join(proc.stdout.strip().splitlines()[-12:]))
                results.append((name, f"pipeline exited {proc.returncode}", False))
                continue
            if baseline is not None:
                want, have = load(baseline), load(out)
                diff = [k for k in want if want[k] != have.get(k)]
                ok = not diff
                detail = "identical to baseline" if ok else f"{len(diff)} answer(s) moved: {diff[:5]}"
            else:
                hits, total = score(questions, out)
                ok = hits == total
                detail = f"{hits}/{total} exact"
            results.append((name, detail, ok))
            print(f"  {detail}")

        print("\n=== permutations ===", flush=True)
        detail, ok = check_permutations(docs, tmp, ROOT / "validation_questions.json")
        results.append(("permutations", detail, ok))
        print(f"  {detail}")

    print("\n=== hostile inputs ===", flush=True)
    proc = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tests/hostile.py"),
                           "--docs", str(docs)],
                          capture_output=True, text=True, cwd=str(ROOT), timeout=1800)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "no output"
    results.append(("hostile", tail, proc.returncode == 0))
    print(f"  {tail}")

    print("\n" + "=" * 64)
    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, detail, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:{width}}  {detail}")
        failed += not ok
    print("=" * 64)
    if failed:
        print(f"  {failed} check(s) failed")
    else:
        print("  everything passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
