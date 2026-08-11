import sqlite3
from src.config import DB_PATH

conn = sqlite3.connect(str(DB_PATH))
engineers = [r[0] for r in conn.execute("SELECT name FROM engineers").fetchall()]
print(f"Total engineers: {len(engineers)}")
print(engineers)
conn.close()
