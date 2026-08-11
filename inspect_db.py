import sqlite3
from src.config import DB_PATH

conn = sqlite3.connect(str(DB_PATH))
cur = conn.cursor()

tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables in company.db:")
for t in tables:
    cnt = cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{t}')").fetchall()]
    print(f"  {t:20s}: {cnt:5d} rows | cols: {cols}")

conn.close()
