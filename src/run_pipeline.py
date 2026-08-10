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
    
    # ── Stage 3: Answer Questions ───────────────────────────────────────
    banner("3/4 — Answering Sample Questions")
    from src.config import SAMPLE_QUESTIONS_PATH, PROJECT_ROOT
    from src.answer_engine import answer_all_questions
    
    output_path = str(PROJECT_ROOT / "submission.jsonl")
    answer_all_questions(str(SAMPLE_QUESTIONS_PATH), output_path)
    
    # ── Stage 4: Score ──────────────────────────────────────────────────
    banner("4/4 — Running Evaluation")
    try:
        eval_path = PROJECT_ROOT / "evaluate.py"
        if eval_path.exists():
            import subprocess
            result = subprocess.run(
                [sys.executable, str(eval_path),
                 "--submission", output_path, "--per-question"],
                capture_output=True, text=True, cwd=str(PROJECT_ROOT))
            print(result.stdout)
            if result.stderr:
                print(result.stderr)
        else:
            print("  evaluate.py not found — skipping scoring")
    except Exception as e:
        print(f"  Evaluation error: {e}")
    
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
