"""WS1 deep-dive: forward decay by series, breakeven math, pending settlements, weekly trend."""
import sqlite3
from collections import defaultdict

con = sqlite3.connect("file:/app/data/sibylla.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

BLOCKED = {"KXLOWTDC","KXLOWTDEN","KXLOWTPHIL","KXLOWTOKC","KXHIGHTNOLA",
           "KXHIGHTSATX","KXLOWTDAL","KXLOWTNOLA","KXHIGHTSFO"}

def q(sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()

import re
def in_zone(r):
    tu = (r["market_ticker"] or "").upper()
    if re.search(r"-T\d", tu):
        return False
    return (r["direction"] == "no" and r["entry_price"] is not None
            and 0.20 <= r["entry_price"] < 0.40
            and tu.split("-")[0] not in BLOCKED)

rows = q("""
    SELECT t.id, t.market_ticker, t.direction, t.entry_price, t.exit_price, t.clv,
           t.contracts, t.pnl, t.prediction_correct, t.entry_time, t.exit_time,
           t.settlement_result, t.settlement_pnl
    FROM trades t LEFT JOIN alerts a ON a.id = t.alert_id
    WHERE t.exit_reason = 'market_closed'
      AND COALESCE(json_extract(a.details, '$.learning_mode'), '') != 'explore'
""")

# 1. The 8 pending-settlement trades from today's startup
print("--- trades with settlement_result NULL/pending (closed but unsettled) ---")
for r in rows:
    if r["settlement_result"] in (None, "", "pending"):
        print(f"id={r['id']} {r['market_ticker']} exit={r['exit_time'][:16]} "
              f"exit_price={r['exit_price']} pnl={r['pnl']} pred={r['prediction_correct']}")
print()

# 2. Weekly P&L/winrate trend for ZONE (real settled only)
real = [r for r in rows if r["settlement_result"] not in (None, "", "pending")]
zone = [r for r in real if in_zone(r)]
g = defaultdict(lambda: [0,0,0.0,0.0,0])
for r in zone:
    wk = (r["entry_time"] or "")[:10]
    # ISO week
    import datetime
    try:
        d = datetime.date.fromisoformat(wk)
        key = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
    except ValueError:
        key = "?"
    g[key][0] += 1
    g[key][1] += 1 if r["prediction_correct"] else 0
    g[key][2] += r["pnl"] or 0
    if r["clv"] is not None:
        g[key][3] += r["clv"]; g[key][4] += 1
print("--- ZONE weekly (by entry week, settled only) ---")
print(f"{'week':10s} {'n':>4s} {'win%':>6s} {'pnl':>8s} {'avg_clv':>8s} {'be%':>6s}")
for k in sorted(g):
    n, w, pnl, clvs, clvn = g[k]
    # breakeven: avg NO cost = 1 - avg entry yes price
    print(f"{k:10s} {n:4d} {w/n*100:6.1f} {pnl:+8.2f} "
          f"{(clvs/clvn*100 if clvn else 0):+7.2f}c")
print()

# 3. Breakeven math per bucket (zone, by period): win% needed vs achieved
def period(r):
    et = (r["entry_time"] or "")[:10]
    return "jun+" if et >= "2026-06-01" else ("may25" if et >= "2026-05-25" else "pre")
g2 = defaultdict(lambda: [0,0,0.0])
for r in zone:
    b = "20-30c" if r["entry_price"] < 0.30 else "30-40c"
    key = (period(r), b)
    g2[key][0] += 1
    g2[key][1] += 1 if r["prediction_correct"] else 0
    g2[key][2] += (1.0 - r["entry_price"])  # NO-side cost
print("--- ZONE breakeven math: (period, bucket) n, win%, needed% ---")
for k in sorted(g2):
    n, w, cost = g2[k]
    print(f"{str(k):20s} n={n:4d} win={w/n*100:5.1f}%  breakeven={cost/n*100:5.1f}%  "
          f"margin={w/n*100 - cost/n*100:+5.1f}pp")
print()

# 4. June zone by series — any slice still positive forward?
g3 = defaultdict(lambda: [0,0,0.0])
for r in zone:
    if period(r) == "pre":
        continue
    s = r["market_ticker"].upper().split("-")[0]
    g3[s][0] += 1
    g3[s][1] += 1 if r["prediction_correct"] else 0
    g3[s][2] += r["pnl"] or 0
print("--- ZONE since May 25 by series (forward window) ---")
for s, (n, w, pnl) in sorted(g3.items(), key=lambda x: -x[1][2]):
    print(f"{s:14s} n={n:3d} win={w/n*100:5.1f}% pnl={pnl:+7.2f}")
print()

# 5. CLV sign convention check: sample winners/losers
print("--- CLV convention sample (NO zone trades) ---")
for r in zone[:5]:
    print(f"entry_yes={r['entry_price']:.2f} exit={r['exit_price']} clv={r['clv']} "
          f"correct={r['prediction_correct']} pnl={r['pnl']}")
print()

# 6. Per-trade pnl distribution (zone, settled): stake + variance for sizing
import statistics
pnls = [r["pnl"] for r in zone if r["pnl"] is not None]
contracts = [r["contracts"] or 1 for r in zone]
per1 = [p/max(1,c) for p, c in zip(pnls, contracts)]
print(f"zone per-contract pnl: mean={statistics.mean(per1):+.4f} "
      f"stdev={statistics.pstdev(per1):.4f} n={len(per1)}")
fw = [r for r in zone if period(r) != "pre"]
pf = [(r["pnl"] or 0)/max(1, r["contracts"] or 1) for r in fw]
print(f"zone FORWARD per-contract pnl: mean={statistics.mean(pf):+.4f} "
      f"stdev={statistics.pstdev(pf):.4f} n={len(pf)}")

# 7. contracts distribution forward
g4 = defaultdict(int)
for r in fw:
    g4[r["contracts"] or 1] += 1
print("forward zone contracts dist:", dict(sorted(g4.items())))
