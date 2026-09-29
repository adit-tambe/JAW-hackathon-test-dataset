import json
import sqlite3
from src.config import DB_PATH

conn = sqlite3.connect(str(DB_PATH))
cur = conn.cursor()

# Create receivables table
cur.execute("""
CREATE TABLE IF NOT EXISTS receivables (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no      TEXT,
    client_name     TEXT,
    client_id       INTEGER REFERENCES clients(client_id),
    invoice_date    TEXT,
    invoiced        REAL,
    status          TEXT,
    received        REAL,
    outstanding     REAL,
    doc_id          TEXT
)
""")

with open('data/extracted/WB-Receivables_Ageing.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

sheet_data = d['sheets']['AR Ageing']['data']

cur.execute("DELETE FROM receivables")

inserted = 0
for row in sheet_data:
    if isinstance(row, dict):
        cname = row.get('Client')
        if not cname:
            continue
        # Find client_id
        c_row = cur.execute("SELECT client_id FROM clients WHERE LOWER(client_name) = LOWER(?) OR client_name LIKE ?", (cname.strip(), f"%{cname.strip()}%")).fetchone()
        client_id = c_row[0] if c_row else None
        
        inv = row.get('Invoiced (INR)') or 0
        rec = row.get('Received (INR)') or 0
        out = row.get('Outstanding (INR)') or 0
        
        cur.execute("""
            INSERT INTO receivables (invoice_no, client_name, client_id, invoice_date, invoiced, status, received, outstanding, doc_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'WB-Receivables_Ageing')
        """, (row.get('Invoice No'), cname, client_id, row.get('Invoice Date'), float(inv), row.get('Status'), float(rec), float(out)))
        inserted += 1

conn.commit()
print(f"Inserted {inserted} receivables records into DB!")

# Test query for a client
for r in cur.execute("""
    SELECT client_name, SUM(invoiced), SUM(received), (SUM(received)/SUM(invoiced))*100
    FROM receivables
    GROUP BY client_name
    LIMIT 5
""").fetchall():
    print(r)

conn.close()
