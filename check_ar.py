import json
import sqlite3
from src.config import DB_PATH

conn = sqlite3.connect(str(DB_PATH))
db_clients = [r[0] for r in conn.execute("SELECT client_name FROM clients").fetchall()]

with open('data/extracted/WB-Receivables_Ageing.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

sheet_data = d['sheets']['AR Ageing']['data']
ar_clients = set(row.get('Client') for row in sheet_data if isinstance(row, dict) and row.get('Client'))

print(f"AR Clients ({len(ar_clients)}):", ar_clients)
print(f"\nDB Clients ({len(db_clients)}):", db_clients)

missing = []
for ac in ar_clients:
    found = False
    for dbc in db_clients:
        if ac.lower() in dbc.lower() or dbc.lower() in ac.lower():
            found = True
            break
    if not found:
        missing.append(ac)

print("\nAR Clients not matched to DB clients:", missing)
conn.close()
