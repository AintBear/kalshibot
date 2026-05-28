import sqlite3
from pathlib import Path

db_path = Path("data/sibylla.db")
if not db_path.exists():
    db_path = Path("/app/data/sibylla.db")

conn = sqlite3.connect(db_path)
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print(f"Tables in {db_path}:")
for t in tables:
    print(" -", t[0])
conn.close()
