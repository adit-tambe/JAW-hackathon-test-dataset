import json
import sqlite3
import re
from datetime import datetime
from statistics import median
from collections import Counter

from src.config import DB_PATH
from src.money import _words_to_number, format_as_answer

# Load questions
with open('BITS-Validation-Dataset/questions.json', 'r', encoding='utf-8') as f:
    qdata = json.load(f)
questions = qdata.get('questions', qdata)

# Connect DB
conn = sqlite3.connect(str(DB_PATH))

# Fetch entities
db_clients = [r[0] for r in conn.execute('SELECT client_name FROM clients').fetchall()]
db_engineers = [r[0] for r in conn.execute('SELECT name FROM engineers').fetchall()]
db_projects = [r[0] for r in conn.execute('SELECT project_name FROM works').fetchall()]
db_categories = [r[0] for r in conn.execute('SELECT DISTINCT work_category FROM works WHERE work_category IS NOT NULL').fetchall()]

print(f"Entities in DB: {len(db_clients)} clients, {len(db_engineers)} engineers, {len(db_projects)} projects, {len(db_categories)} categories")
print("Categories:", db_categories)

conn.close()
