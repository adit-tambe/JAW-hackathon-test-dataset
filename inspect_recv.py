import sqlite3
conn = sqlite3.connect('data/company.db')

print("=== PHED WB receivables ===")
rows = conn.execute("""
    SELECT client_id, client_name, SUM(outstanding), COUNT(*) 
    FROM receivables WHERE client_name LIKE '%West Bengal%' 
    GROUP BY client_id, client_name
""").fetchall()
for r in rows:
    print(f"  client_id={r[0]}, name={r[1]}, outstanding={r[2]}, count={r[3]}")

print("\n=== Maharashtra Municipal receivables ===")
rows2 = conn.execute("""
    SELECT client_id, client_name, SUM(outstanding), SUM(invoiced), SUM(received) 
    FROM receivables WHERE client_name LIKE '%Maharashtra Municipal%' 
    GROUP BY client_id, client_name
""").fetchall()
for r in rows2:
    print(f"  client_id={r[0]}, name={r[1]}, outstanding={r[2]}, invoiced={r[3]}, received={r[4]}")

print("\n=== HV-IC-0276 — Meera Roy, negative unbilled ===")
# Find what engineer 'Meera Roy' maps to
eng = conn.execute("SELECT engineer_id, name FROM engineers WHERE name LIKE '%Meera%'").fetchall()
print(f"  Engineers matching 'Meera': {eng}")
if eng:
    eid = eng[0][0]
    # Find works
    works = conn.execute("""
        SELECT w.client_id, c.client_name, SUM(w.contract_value)
        FROM works w 
        JOIN engineer_works ew ON w.work_id = ew.work_id
        JOIN clients c ON w.client_id = c.client_id
        WHERE ew.engineer_id = ?
        GROUP BY w.client_id
    """, (eid,)).fetchall()
    for w in works:
        print(f"  client_id={w[0]}, name={w[1]}, total_contract={w[2]}")

print("\n=== All clients with negative outstanding total ===")
rows3 = conn.execute("""
    SELECT client_name, SUM(outstanding) as total
    FROM receivables GROUP BY client_name
    HAVING total < 0
""").fetchall()
for r in rows3:
    print(f"  {r[0]}: outstanding={r[1]}")

print("\n=== All clients with negative individual outstanding rows ===")
rows4 = conn.execute("""
    SELECT DISTINCT client_name FROM receivables WHERE outstanding < 0
""").fetchall()
for r in rows4:
    all_outstanding = conn.execute("SELECT SUM(outstanding) FROM receivables WHERE client_name = ?", (r[0],)).fetchone()[0]
    pos_outstanding = conn.execute("SELECT SUM(outstanding) FROM receivables WHERE client_name = ? AND outstanding > 0", (r[0],)).fetchone()[0]
    print(f"  {r[0]}: signed_total={all_outstanding}, positive_only={pos_outstanding}")

conn.close()
