"""WS1 scratch: dump schema + basic counts (read-only)."""
import sqlite3

con = sqlite3.connect("file:/app/data/sibylla.db?mode=ro", uri=True)
cur = con.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("TABLES:", tables)

for t in ("trades", "alerts", "model_calibration", "adaptive_segments"):
    if t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        cols = [r[1] for r in cur.fetchall()]
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        n = cur.fetchone()[0]
        print(f"\n{t} ({n} rows):")
        print("  ", cols)

print("\ntrade status counts:")
cur.execute("SELECT status, COUNT(*) FROM trades GROUP BY status")
for r in cur.fetchall():
    print("  ", r)

print("\nexit_reason counts:")
cur.execute("SELECT exit_reason, COUNT(*) FROM trades GROUP BY exit_reason ORDER BY 2 DESC")
for r in cur.fetchall():
    print("  ", r)
