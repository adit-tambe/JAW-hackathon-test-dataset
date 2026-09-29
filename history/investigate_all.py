import json
import sqlite3
import re
from collections import Counter

from src.config import DB_PATH

with open('BITS-Validation-Dataset/questions.json', 'r', encoding='utf-8') as f:
    qdata = json.load(f)
questions = qdata.get('questions', qdata)

print(f"Total questions: {len(questions)}")

# Let's inspect the questions by looking at keywords / patterns
categories = Counter()
uncategorized = []

for q in questions:
    qid = q['qid']
    atype = q.get('answer_type')
    text = q['question']
    qlow = text.lower()

    # Pattern checks
    matched = False
    
    # 1. Category comparison (difference between scope A and scope B for a client)
    if ('difference' in qlow or 'spread' in qlow or 'variance' in qlow) and (' versus ' in qlow or ' vs ' in qlow or ' and ' in qlow or ' compared to ' in qlow) and ('scope' in qlow or 'work' in qlow or 'category' in qlow or 'between' in qlow):
        categories['category_difference'] += 1
        matched = True
    elif 'collection' in qlow or 'collected' in qlow or 'cleared against' in qlow or 'out of 100' in qlow:
        categories['collection_percent'] += 1
        matched = True
    elif 'shortfall' in qlow or 'invoiced' in qlow or 'billed' in qlow and ('gap' in qlow or 'sanctioned' in qlow or 'awarded' in qlow or 'approved' in qlow):
        categories['unbilled_gap'] += 1
        matched = True
    elif 'mean' in qlow and 'median' in qlow:
        categories['mean_median_diff'] += 1
        matched = True
    elif 'between 20' in qlow or 'moved between' in qlow or 'value moved' in qlow:
        categories['yearly_diff'] += 1
        matched = True
    elif 'days' in qlow or 'interval' in qlow or 'elapsed' in qlow:
        categories['date_span'] += 1
        matched = True
    elif 'distinct' in qlow or 'categories' in qlow and 'led to completion' in qlow:
        categories['distinct_count'] += 1
        matched = True
    elif 'excluding' in qlow or 'remove the' in qlow or 'without' in qlow:
        categories['exclusion_aggregate'] += 1
        matched = True
    elif 'average size' in qlow or 'mean size' in qlow or 'average across' in qlow or 'avg across' in qlow:
        categories['avg_work_size'] += 1
        matched = True
    elif 'exceed' in qlow or 'clear' in qlow or 'cutoff' in qlow or 'threshold' in qlow or 'mark' in qlow or 'limit' in qlow:
        categories['threshold_aggregate'] += 1
        matched = True
    elif 'lack' in qlow or 'missing' in qlow or 'no client reference' in qlow:
        categories['absence'] += 1
        matched = True
    elif 'share' in qlow and 'reference' in qlow:
        categories['referenced_share'] += 1
        matched = True
    elif 'shortfall' in qlow or 'target' in qlow or 'reach' in qlow:
        categories['gap_to_threshold'] += 1
        matched = True
    elif 'largest' in qlow and ('exceed' in qlow or 'difference' in qlow):
        categories['rank_value'] += 1
        matched = True
    elif 'as prime' in qlow or 'sub-contractor' in qlow:
        categories['role_split'] += 1
        matched = True
    elif 'combined value' in qlow or 'total value' in qlow or 'sum' in qlow or 'aggregate' in qlow:
        categories['general_aggregate'] += 1
        matched = True

    if not matched:
        uncategorized.append((qid, atype, text))

print("\n--- CATEGORY BREAKDOWN ---")
for cat, count in categories.most_common():
    print(f"{cat:25s}: {count}")

print(f"\nUncategorized: {len(uncategorized)}")
for qid, atype, text in uncategorized[:20]:
    print(f"[{qid}] ({atype}): {text}")
