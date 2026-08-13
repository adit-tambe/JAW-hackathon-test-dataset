import sqlite3
conn = sqlite3.connect('data/company.db')

print("=== Clients table: West Bengal entries ===")
r = conn.execute("SELECT client_id, client_name FROM clients WHERE client_name LIKE '%West Bengal%'").fetchall()
for row in r:
    print(f"  {row}")

print("\n=== Receivables table: West Bengal entries ===")
r2 = conn.execute("SELECT DISTINCT client_id, client_name FROM receivables WHERE client_name LIKE '%West Bengal%'").fetchall()
for row in r2:
    print(f"  {row}")

print("\n=== PHED WB: What does find_client_id return? ===")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from src.answer_engine import find_client_id
cid = find_client_id(conn, 'Public Health Engineering Dept, West Bengal')
print(f"  find_client_id('Public Health Engineering Dept, West Bengal') = {cid}")
if cid:
    cname = conn.execute("SELECT client_name FROM clients WHERE client_id = ?", (cid,)).fetchone()
    print(f"  That maps to: {cname}")
    # Check outstanding for this client_id
    r3 = conn.execute("SELECT SUM(outstanding) FROM receivables WHERE client_id = ?", (cid,)).fetchone()
    print(f"  Outstanding by client_id={cid}: {r3[0]}")
    
# Check by name
r4 = conn.execute("SELECT SUM(outstanding) FROM receivables WHERE LOWER(client_name) = LOWER(?)", 
                   ('Public Health Engineering Dept, West Bengal',)).fetchone()
print(f"  Outstanding by exact name: {r4[0]}")

# Check what client_id the receivables rows actually have for this name
r5 = conn.execute("SELECT client_id, COUNT(*) FROM receivables WHERE LOWER(client_name) = LOWER(?)", 
                   ('Public Health Engineering Dept, West Bengal',)).fetchall()
print(f"  Receivables by exact name: client_id={r5}")

r6 = conn.execute("SELECT client_id, COUNT(*), SUM(outstanding) FROM receivables WHERE LOWER(client_name) = LOWER(?) GROUP BY client_id", 
                   ('Public Health Engineering Dept, West Bengal',)).fetchall()
print(f"  Grouped: {r6}")

conn.close()
