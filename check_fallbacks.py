import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.answer_engine import parse_question, answer_question, fallback_answer

conn = sqlite3.connect('data/company.db')
with open('BITS-Validation-Dataset/questions.json', 'r', encoding='utf-8') as f:
    vdata = json.load(f)
vquestions = vdata.get('questions', vdata) if isinstance(vdata, dict) else vdata

fallbacks = 0
for q in vquestions:
    qid = q['qid']
    question = q['question']
    answer_type = q.get('answer_type', 'money')
    
    params = parse_question(conn, question)
    ans = answer_question(conn, question, qid, answer_type=answer_type)
    
    # We can detect a fallback if the handler returns None or 0 (when ZERO_IS_MEANINGFUL=False)
    # Let's just mock the handler to see what it would return
    from src.answer_engine import SHAPE_HANDLERS, reconcile_shape, ZERO_IS_MEANINGFUL
    
    shape = params.get("question_shape", "other")
    if answer_type:
        shape = reconcile_shape(shape, answer_type, params)
    
    handler = SHAPE_HANDLERS.get(shape)
    handler_ans = None
    if handler:
        try:
            handler_ans = handler(conn, params)
        except Exception:
            pass
            
    is_fallback = False
    if handler_ans is None or (not handler_ans and shape not in ZERO_IS_MEANINGFUL):
        is_fallback = True
        
    if is_fallback:
        print(f"[{qid}] FALLBACK TRIGGERED! shape={shape}, answer={ans}")
        print(f"  Q: {question[:150]}")
        fallbacks += 1

print(f"\nTotal fallbacks: {fallbacks}")
