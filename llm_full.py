"""
llm_full.py — Run ALL 333 questions through the LLM text-to-SQL pipeline.
Compare each answer to the rule engine to find discrepancies.
Use the free tier with 13s delay between calls.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.llm_text2sql import answer_question_llm, load_cache, save_cache, _cache
from src.answer_engine import answer_question as answer_question_rule

conn = sqlite3.connect('data/company.db')
load_cache()

# Load validation questions
for qpath in ['BITS-Validation-Dataset/questions.json', 'questions.json']:
    try:
        with open(qpath, 'r', encoding='utf-8') as f:
            vdata = json.load(f)
        vquestions = vdata.get('questions', vdata) if isinstance(vdata, dict) else vdata
        break
    except FileNotFoundError:
        continue

print(f"Running {len(vquestions)} questions through BOTH engines...")
print(f"Cached LLM answers: {len(_cache)}")

diffs = []
llm_results = {}
rule_results = {}

for i, q in enumerate(vquestions):
    qid = q['qid']
    question = q['question']
    answer_type = q.get('answer_type', 'money')
    
    rule_ans = answer_question_rule(conn, question, qid, answer_type=answer_type)
    
    # Check if cached
    from src.llm_text2sql import cache_key
    ck = cache_key(question, answer_type)
    if ck in _cache:
        llm_ans = _cache[ck].get('answer', 0)
    else:
        try:
            llm_ans = answer_question_llm(question, answer_type, qid)
            save_cache()
            time.sleep(13)  # Rate limit
        except Exception as e:
            print(f"  [{qid}] LLM ERROR: {e}")
            llm_ans = rule_ans  # fallback
    
    rule_results[qid] = rule_ans
    llm_results[qid] = llm_ans
    
    if rule_ans != llm_ans:
        diffs.append((qid, rule_ans, llm_ans, answer_type, question[:100]))
        print(f"  DIFF [{qid}] Rule={rule_ans} LLM={llm_ans} type={answer_type}")
        print(f"       Q: {question[:100]}")
    
    if (i + 1) % 50 == 0:
        print(f"  [{i+1}/{len(vquestions)}] processed, {len(diffs)} diffs so far, {len(_cache)} cached")
        save_cache()

save_cache()

print(f"\n{'='*60}")
print(f"SUMMARY: {len(diffs)} questions differ between Rule and LLM")
print(f"{'='*60}")
for qid, rule, llm, atype, q in diffs:
    print(f"  [{qid}] Rule={rule:>15} LLM={llm:>15} type={atype}")

# Generate LLM-only submission
with open('submission_llm.csv', 'w', encoding='utf-8') as f:
    f.write('question_id,answer\n')
    for q in vquestions:
        qid = q['qid']
        ans = llm_results.get(qid, 0)
        f.write(f"{qid},{ans}\n")
print(f"\nGenerated submission_llm.csv (LLM-only)")

# Generate hybrid: prefer LLM when they disagree (since rule got 97.2%, maybe LLM is better on diffs)
with open('submission_hybrid.csv', 'w', encoding='utf-8') as f:
    f.write('question_id,answer\n')
    for q in vquestions:
        qid = q['qid']
        # Use LLM answer where they differ, otherwise rule
        if qid in [d[0] for d in diffs]:
            ans = llm_results[qid]
        else:
            ans = rule_results[qid]
        f.write(f"{qid},{ans}\n")
print(f"Generated submission_hybrid.csv (LLM for diffs, Rule otherwise)")

# Generate inverse hybrid: prefer Rule when they disagree
with open('submission_hybrid_rule.csv', 'w', encoding='utf-8') as f:
    f.write('question_id,answer\n')
    for q in vquestions:
        qid = q['qid']
        ans = rule_results[qid]
        f.write(f"{qid},{ans}\n")
print(f"Generated submission_hybrid_rule.csv (Rule always = same as current)")

conn.close()
