import json
import sqlite3
from src.answer_engine import answer_question

def generate():
    conn = sqlite3.connect('data/company.db')
    
    # 1. Sample questions
    with open('sample_questions.json', 'r', encoding='utf-8') as f:
        sdata = json.load(f)
    squestions = sdata.get('questions', sdata) if isinstance(sdata, dict) else sdata
    
    with open('sample_sub.jsonl', 'w', encoding='utf-8') as f:
        for q in squestions:
            ans = answer_question(conn, q['question'], q['qid'])
            f.write(json.dumps({'qid': q['qid'], 'answer': ans}) + '\n')
            
    print(f"Sample sub: {len(squestions)} rows written.")

    # 2. Validation / Test questions
    for qpath in ['questions.json', 'BITS-Validation-Dataset/questions.json']:
        try:
            with open(qpath, 'r', encoding='utf-8') as f:
                vdata = json.load(f)
            vquestions = vdata.get('questions', vdata) if isinstance(vdata, dict) else vdata
            
            with open('submission.csv', 'w', encoding='utf-8') as f:
                f.write('question_id,answer\n')
                for q in vquestions:
                    ans = answer_question(conn, q['question'], q['qid'])
                    f.write(f"{q['qid']},{ans}\n")
            print(f"Submission CSV: {len(vquestions)} rows written from {qpath}.")
            break
        except FileNotFoundError:
            continue

    conn.close()

if __name__ == '__main__':
    generate()
