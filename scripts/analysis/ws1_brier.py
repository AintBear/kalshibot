"""WS1: does the model beat the market? Brier scores + edge realization.

For every settled non-explore trade with a linked alert, compare:
  - market price at alert time (market_price = YES prob implied)
  - model_prob (post-calibration at the time) and raw_model_prob
against actual YES settlement. Lower Brier = better.
Also: did trades with higher model-edge actually win more?
"""
import sqlite3
import re
from collections import defaultdict

con = sqlite3.connect("file:/app/data/sibylla.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

rows = cur.execute("""
    SELECT t.id, t.market_ticker, t.direction, t.entry_price, t.pnl,
           t.prediction_correct, t.entry_time, t.settlement_result,
           a.market_price, a.model_prob,
           json_extract(a.details, '$.raw_model_prob') raw_p
    FROM trades t JOIN alerts a ON a.id = t.alert_id
    WHERE t.exit_reason = 'market_closed'
      AND t.settlement_result IS NOT NULL AND t.settlement_result != 'pending'
      AND COALESCE(json_extract(a.details, '$.learning_mode'), '') != 'explore'
      AND a.market_price IS NOT NULL AND a.model_prob IS NOT NULL
""").fetchall()

def yes_won(r):
    s = r["settlement_result"]
    return 1.0 if s in ("yes", "1", 1, True) else 0.0

def period(r):
    et = (r["entry_time"] or "")[:10]
    return "fwd_may25+" if et >= "2026-05-25" else "pre"

print(f"n with alert linkage: {len(rows)}")
buckets = defaultdict(list)
for r in rows:
    buckets[period(r)].append(r)
buckets["ALL"] = rows

for k in ("ALL", "pre", "fwd_may25+"):
    rs = buckets[k]
    if not rs:
        continue
    n = len(rs)
    bm = sum((r["market_price"] - yes_won(r))**2 for r in rs) / n
    bmod = sum((r["model_prob"] - yes_won(r))**2 for r in rs) / n
    braw = [r for r in rs if r["raw_p"] is not None]
    brs = sum((float(r["raw_p"]) - yes_won(r))**2 for r in braw) / max(1, len(braw))
    print(f"{k:12s} n={n:4d}  Brier market={bm:.4f}  model={bmod:.4f}  "
          f"raw={brs:.4f} (raw n={len(braw)})  "
          f"{'MODEL BEATS MARKET' if bmod < bm else 'market beats model'}")
print()

# Edge realization: group trades by model edge (side_prob - side_cost) deciles
print("--- edge realization: does bigger model edge mean better outcomes? ---")
g = defaultdict(lambda: [0, 0, 0.0])
for r in rows:
    side_prob = 1 - r["model_prob"] if r["direction"] == "no" else r["model_prob"]
    side_cost = 1 - r["entry_price"] if r["direction"] == "no" else r["entry_price"]
    edge = side_prob - side_cost
    b = "neg" if edge < 0 else ("0-5c" if edge < .05 else ("5-10c" if edge < .10 else ("10-20c" if edge < .20 else "20c+")))
    g[b][0] += 1
    g[b][1] += 1 if r["prediction_correct"] else 0
    g[b][2] += r["pnl"] or 0
for b in ("neg", "0-5c", "5-10c", "10-20c", "20c+"):
    if b not in g:
        continue
    n, w, pnl = g[b]
    print(f"edge {b:7s} n={n:4d} win={w/n*100:5.1f}% pnl={pnl:+8.2f}")
print()

# Same but forward only
print("--- edge realization FORWARD (may25+) ---")
g = defaultdict(lambda: [0, 0, 0.0])
for r in buckets["fwd_may25+"]:
    side_prob = 1 - r["model_prob"] if r["direction"] == "no" else r["model_prob"]
    side_cost = 1 - r["entry_price"] if r["direction"] == "no" else r["entry_price"]
    edge = side_prob - side_cost
    b = "neg" if edge < 0 else ("0-5c" if edge < .05 else ("5-10c" if edge < .10 else ("10-20c" if edge < .20 else "20c+")))
    g[b][0] += 1
    g[b][1] += 1 if r["prediction_correct"] else 0
    g[b][2] += r["pnl"] or 0
for b in ("neg", "0-5c", "5-10c", "10-20c", "20c+"):
    if b not in g:
        continue
    n, w, pnl = g[b]
    print(f"edge {b:7s} n={n:4d} win={w/n*100:5.1f}% pnl={pnl:+8.2f}")
print()

# Market-only baseline: if we had just bet NO when market YES in 20-40c, what
# would the market price alone predict? settlement YES rate vs price bucket.
print("--- market price vs actual settlement (all settled trades, by mkt price) ---")
g2 = defaultdict(lambda: [0, 0.0, 0.0])
for r in rows:
    b = round(r["market_price"], 1)
    g2[b][0] += 1
    g2[b][1] += yes_won(r)
    g2[b][2] += r["market_price"]
for b in sorted(g2):
    n, w, s = g2[b]
    print(f"mkt~{b:.1f}: n={n:4d} implied={s/n:.3f} actual_yes={w/n:.3f} gap={w/n - s/n:+.3f}")
