import json
import sqlite3
import sys
from pathlib import Path
from src.answer_engine import answer_question

def main():
    conn = sqlite3.connect('data/company.db')
    
    # 1. Evaluate sample_questions.json
    with open('sample_questions.json', 'r', encoding='utf-8') as f:
        sdata = json.load(f)
    squestions = sdata.get('questions', sdata)
    
    with open('sample_sub.jsonl', 'w', encoding='utf-8') as f:
        for q in squestions:
            ans = answer_question(conn, q['question'], q['qid'])
            f.write(json.dumps({'qid': q['qid'], 'answer': ans}) + '\n')
            
    print(f"Wrote {len(squestions)} sample answers to sample_sub.jsonl")

    # 2. Evaluate validation questions.json
    qfile = 'questions.json' if Path('questions.json').exists() else 'BITS-Validation-Dataset/questions.json'
    with open(qfile, 'r', encoding='utf-8') as f:
        vdata = json.load(f)
    vquestions = vdata.get('questions', vdata)
    
    with open('submission.jsonl', 'w', encoding='utf-8') as f:
        for q in vquestions:
            ans = answer_question(conn, q['question'], q['qid'])
            f.write(json.dumps({'qid': q['qid'], 'answer': ans}) + '\n')
            
    with open('submission.csv', 'w', encoding='utf-8') as f:
        f.write('question_id,answer\n')
        for q in vquestions:
            ans = answer_question(conn, q['question'], q['qid'])
            f.write(f"{q['qid']},{ans}\n")

    print(f"Wrote {len(vquestions)} validation answers to submission.jsonl and submission.csv")
    conn.close()

if __name__ == '__main__':
    main()
