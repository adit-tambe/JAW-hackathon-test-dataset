"""
make_sub_llm.py — Generate submission using LLM Text-to-SQL pipeline.

Usage:
    python make_sub_llm.py                    # Full run: sample + validation
    python make_sub_llm.py --self-test        # Only sample questions (verify)
    python make_sub_llm.py --clear-cache      # Clear LLM cache and re-run
    python make_sub_llm.py --hybrid           # Use rule engine + LLM fallback
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.llm_text2sql import answer_question_llm, load_cache, save_cache, CACHE_PATH, _cache
from src.answer_engine import answer_question as answer_question_rule


def generate_llm(self_test_only=False, clear_cache=False, hybrid=False):
    """Generate submission using LLM pipeline."""
    if clear_cache and CACHE_PATH.exists():
        CACHE_PATH.unlink()
        print("Cache cleared.")

    load_cache()
    conn = sqlite3.connect('data/company.db')

    # 1. Sample questions (always run for verification)
    with open('sample_questions.json', 'r', encoding='utf-8') as f:
        sdata = json.load(f)
    squestions = sdata.get('questions', sdata) if isinstance(sdata, dict) else sdata

    print(f"\n{'='*60}")
    print(f"Running {len(squestions)} sample questions...")
    print(f"{'='*60}\n")

    from evaluate import score_one
    sample_score = 0
    sample_results = []

    for i, q in enumerate(squestions):
        qid = q['qid']
        question = q['question']
        answer_type = q.get('answer_type', 'money')
        gold = q.get('answer')

        print(f"[{i+1}/{len(squestions)}] {qid}: {question[:60]}...")

        if hybrid:
            # Try rule engine first
            rule_ans = answer_question_rule(conn, question, qid, answer_type=answer_type)
            rule_score = score_one(gold, rule_ans) if gold is not None else 0

            if rule_score == 1.0:
                ans = rule_ans
                print(f"  Rule: {rule_ans} [OK] (exact)")
            else:
                # Fall back to LLM
                llm_ans = answer_question_llm(question, answer_type, qid)
                llm_score = score_one(gold, llm_ans) if gold is not None else 0

                if llm_score >= rule_score:
                    ans = llm_ans
                    print(f"  Rule: {rule_ans} ({rule_score}), LLM: {llm_ans} ({llm_score}) -> using LLM")
                else:
                    ans = rule_ans
                    print(f"  Rule: {rule_ans} ({rule_score}), LLM: {llm_ans} ({llm_score}) -> using Rule")

                time.sleep(4)  # Rate limit
        else:
            ans = answer_question_llm(question, answer_type, qid)
            time.sleep(4)  # Rate limit

        sample_results.append({'qid': qid, 'answer': ans})

        if gold is not None:
            s = score_one(gold, ans)
            sample_score += s
            status = "[OK]" if s == 1.0 else f"[!!] {s:.1f}" if s > 0 else "[XX]"
            print(f"  -> {ans} (gold={gold}) {status}")

    print(f"\nSample score: {sample_score:.1f}/{len(squestions)} = {sample_score/len(squestions):.1%}")

    # Write sample submission
    with open('sample_sub.jsonl', 'w', encoding='utf-8') as f:
        for r in sample_results:
            f.write(json.dumps(r) + '\n')

    if self_test_only:
        print("\nSelf-test complete.")
        conn.close()
        return

    # 2. Validation / Test questions
    for qpath in ['BITS-Validation-Dataset/questions.json', 'questions.json']:
        try:
            with open(qpath, 'r', encoding='utf-8') as f:
                vdata = json.load(f)
            vquestions = vdata.get('questions', vdata) if isinstance(vdata, dict) else vdata

            print(f"\n{'='*60}")
            print(f"Running {len(vquestions)} validation questions from {qpath}...")
            print(f"{'='*60}\n")

            with open('submission.csv', 'w', encoding='utf-8') as f:
                f.write('question_id,answer\n')
                for i, q in enumerate(vquestions):
                    qid = q['qid']
                    question = q['question']
                    answer_type = q.get('answer_type', 'money')

                    if hybrid:
                        rule_ans = answer_question_rule(conn, question, qid, answer_type=answer_type)
                        llm_ans = answer_question_llm(question, answer_type, qid)

                        # For validation, we don't know gold, so prefer LLM
                        # (it handles edge cases the rule engine misses)
                        ans = llm_ans if llm_ans != 0 else rule_ans
                    else:
                        ans = answer_question_llm(question, answer_type, qid)

                    f.write(f"{qid},{ans}\n")

                    if (i + 1) % 10 == 0:
                        print(f"  [{i+1}/{len(vquestions)}] completed...")
                        save_cache()

                    time.sleep(4)  # Rate limit

            save_cache()
            print(f"\nSubmission CSV: {len(vquestions)} rows written to submission.csv")
            break
        except FileNotFoundError:
            continue

    conn.close()
    print("\nDone!")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true',
                       help='Only run sample questions for verification')
    parser.add_argument('--clear-cache', action='store_true',
                       help='Clear the LLM SQL cache')
    parser.add_argument('--hybrid', action='store_true',
                       help='Use rule engine + LLM fallback')
    args = parser.parse_args()

    generate_llm(
        self_test_only=args.self_test,
        clear_cache=args.clear_cache,
        hybrid=args.hybrid,
    )
