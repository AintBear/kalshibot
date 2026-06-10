"""WS1: which signals have genuine forward value?
1. intraday observation override trades (iron-clad info edge)
2. hours_to_close at entry vs outcome
3. source disagreement vs outcome
4. NWS-vs-market disagreement direction
"""
import sqlite3
from collections import defaultdict

con = sqlite3.connect("file:/app/data/sibylla.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

rows = cur.execute("""
    SELECT t.id, t.market_ticker, t.direction, t.entry_price, t.pnl,
           t.prediction_correct, t.entry_time, t.contracts,
           a.market_price, a.model_prob,
           json_extract(a.details, '$.intraday_observation') intraday,
           json_extract(a.details, '$.raw_forecast_prob') raw_fc_p,
           json_extract(a.details, '$.hours_to_close') htc,
           json_extract(a.details, '$.source_disagreement') src_dis,
           json_extract(a.details, '$.forecast_sources') sources
    FROM trades t JOIN alerts a ON a.id = t.alert_id
    WHERE t.exit_reason = 'market_closed'
      AND t.settlement_result IS NOT NULL AND t.settlement_result != 'pending'
      AND COALESCE(json_extract(a.details, '$.learning_mode'), '') != 'explore'
""").fetchall()
print(f"n: {len(rows)}")

def stats(rs, label):
    if not rs:
        print(f"{label:34s} n=0")
        return
    n = len(rs)
    w = sum(1 for r in rs if r["prediction_correct"])
    pnl = sum(r["pnl"] or 0 for r in rs)
    pc = sum((r["pnl"] or 0)/max(1, r["contracts"] or 1) for r in rs)/n
    print(f"{label:34s} n={n:4d} win={w/n*100:5.1f}% pnl={pnl:+8.2f} per-ct={pc*100:+5.2f}c")

# 1. intraday override present?
withobs = [r for r in rows if r["intraday"] is not None]
print(f"trades with intraday_observation field: {len(withobs)}")
moved = []
for r in withobs:
    if r["raw_fc_p"] is not None and r["model_prob"] is not None:
        # raw forecast prob vs final model prob differs => override or calib moved it
        import json as _j
        pass
stats(withobs, "has intraday observation data")
stats([r for r in rows if r["intraday"] is None], "no intraday data")
print()

# 2. hours-to-close buckets
g = defaultdict(list)
for r in rows:
    try:
        h = float(r["htc"])
    except (TypeError, ValueError):
        g["unknown"].append(r); continue
    b = "<4h" if h < 4 else ("4-12h" if h < 12 else ("12-24h" if h <= 24 else ">24h"))
    g[b].append(r)
print("--- by hours_to_close at entry ---")
for b in ("<4h", "4-12h", "12-24h", ">24h", "unknown"):
    stats(g.get(b, []), f"htc {b}")
print()

# forward only
fwd = [r for r in rows if (r["entry_time"] or "")[:10] >= "2026-05-25"]
g = defaultdict(list)
for r in fwd:
    try:
        h = float(r["htc"])
    except (TypeError, ValueError):
        g["unknown"].append(r); continue
    b = "<4h" if h < 4 else ("4-12h" if h < 12 else ("12-24h" if h <= 24 else ">24h"))
    g[b].append(r)
print("--- FORWARD (may25+) by hours_to_close ---")
for b in ("<4h", "4-12h", "12-24h", ">24h", "unknown"):
    stats(g.get(b, []), f"htc {b}")
print()

# 3. source disagreement
g = defaultdict(list)
for r in rows:
    try:
        d = float(r["src_dis"])
    except (TypeError, ValueError):
        g["unknown"].append(r); continue
    b = "<1F" if d < 1 else ("1-2F" if d < 2 else ("2-4F" if d < 4 else "4F+"))
    g[b].append(r)
print("--- by source disagreement ---")
for b in ("<1F", "1-2F", "2-4F", "4F+", "unknown"):
    stats(g.get(b, []), f"disagree {b}")
