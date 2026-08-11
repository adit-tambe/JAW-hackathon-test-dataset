"""
run_pipeline.py — Master orchestrator for the BITS Hackathon pipeline.

Runs all stages in order:
  1. Extract PDFs (local regex-based, ~5 seconds)
  2. Extract Excel workbooks
  3. Build SQLite database
  4. Answer sample questions and score
  5. Generate submission.jsonl

Usage:
    python src/run_pipeline.py            # Full pipeline
    python src/run_pipeline.py --skip-extract  # Skip extraction (reuse cached JSONs)
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def banner(text: str):
    """Print a visible stage banner."""
    width = 60
    print()
    print("=" * width)
    print(f"  STAGE: {text}")
    print("=" * width)
    print()


def run_pipeline(skip_extract=False):
    """Run the entire pipeline end-to-end."""
    start = time.time()
    
    print()
    print("=" * 60)
    print("  BITS HACKATHON — Bid Intelligence Pipeline")
    print("=" * 60)
    print()
    
    # ── Stage 1: Extract PDFs ───────────────────────────────────────────
    if not skip_extract:
        banner("1/4 — Extracting PDFs (local regex)")
        from src.extract_local_fast import run_fast_extraction
        run_fast_extraction()
        
        # ── Stage 1b: Extract Workbooks ─────────────────────────────────
        banner("1b/4 — Extracting Excel Workbooks")
        try:
            from src.extract_workbooks import extract_all_workbooks
            extract_all_workbooks()
        except ImportError:
            print("  extract_workbooks.py not found — skipping")
        except Exception as e:
            print(f"  Workbook extraction error: {e}")
    else:
        print("  [SKIPPED] Extraction (using cached data)\n")
    
    # ── Stage 2: Build Database ─────────────────────────────────────────
    banner("2/4 — Building SQLite Database")
    from src.build_db import build_database, validate_database
    conn = build_database()
    validate_database(conn)
    conn.close()
    
    from src.config import SAMPLE_QUESTIONS_PATH, PROJECT_ROOT

    # ── Stage 3: Audit the database against an independent source ───────
    # This runs before scoring on purpose: the sample questions exercise only
    # a third of the clients, so they cannot catch a field that is wrong
    # across the corpus. The credentials pack can.
    banner("3/5 — Auditing the database (independent reconciliation)")
    import subprocess
    audit_path = PROJECT_ROOT / "audit.py"
    if audit_path.exists():
        result = subprocess.run([sys.executable, "-X", "utf8", str(audit_path)],
                                capture_output=True, text=True, cwd=str(PROJECT_ROOT))
        print(result.stdout)
        if result.returncode != 0:
            print("  WARNING: audit reported problems — see above")
    else:
        print("  audit.py not found — skipping")

    # ── Stage 4: Answer Questions ───────────────────────────────────────
    banner("4/5 — Answering Sample Questions")
    from src.answer_engine import answer_all_questions

    # Written to sample-specific names on purpose. submission.csv is the real
    # deliverable, built from the validation questions, and must not be
    # overwritten by a sample run.
    output_path = str(PROJECT_ROOT / "sample_answers.jsonl")
    answer_all_questions(str(SAMPLE_QUESTIONS_PATH), output_path)
    answer_all_questions(str(SAMPLE_QUESTIONS_PATH),
                         str(PROJECT_ROOT / "sample_answers.csv"))

    # ── Stage 4b: the real deliverable, when the question set is present ─
    validation = PROJECT_ROOT / "validation_questions.json"
    if validation.exists():
        print()
        answer_all_questions(str(validation), str(PROJECT_ROOT / "submission.csv"))
    else:
        print("\n  validation_questions.json not present — submission.csv left "
              "untouched")

    # ── Stage 5: Score, under both metrics ──────────────────────────────
    banner("5/5 — Running Evaluation")
    for script, label in (("evaluate.py", "bundled scorer (banded)"),
                          ("score_official.py", "official formula (continuous)")):
        path = PROJECT_ROOT / script
        if not path.exists():
            print(f"  {script} not found — skipping")
            continue
        print(f"--- {label} ---")
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(path),
             "--submission", output_path, "--per-question"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT))
        print(result.stdout)
        if result.stderr:
            print(result.stderr)

    # ── Done ────────────────────────────────────────────────────────────
    elapsed = time.time() - start
    print()
    print("=" * 60)
    print(f"  PIPELINE COMPLETE in {elapsed:.1f}s")
    print(f"  Submission: {output_path}")
    print("=" * 60)
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Run the full BITS Hackathon pipeline")
    parser.add_argument("--skip-extract", action="store_true",
                       help="Skip extraction, reuse cached JSONs")
    args = parser.parse_args()
    run_pipeline(skip_extract=args.skip_extract)


if __name__ == "__main__":
    main()
