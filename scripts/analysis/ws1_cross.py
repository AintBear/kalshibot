"""WS1: cross-tab htc x zone x bucket, and statistical confidence."""
import sqlite3, re, math
from collections import defaultdict

con = sqlite3.connect("file:/app/data/sibylla.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

BLOCKED = {"KXLOWTDC","KXLOWTDEN","KXLOWTPHIL","KXLOWTOKC","KXHIGHTNOLA",
           "KXHIGHTSATX","KXLOWTDAL","KXLOWTNOLA","KXHIGHTSFO"}

rows = cur.execute("""
    SELECT t.market_ticker, t.direction, t.entry_price, t.pnl, t.contracts,
           t.prediction_correct, t.entry_time,
           json_extract(a.details, '$.hours_to_close') htc
    FROM trades t JOIN alerts a ON a.id = t.alert_id
    WHERE t.exit_reason = 'market_closed'
      AND t.settlement_result IS NOT NULL AND t.settlement_result != 'pending'
      AND COALESCE(json_extract(a.details, '$.learning_mode'), '') != 'explore'
""").fetchall()

def in_zone(r):
    tu = (r["market_ticker"] or "").upper()
    if re.search(r"-T\d", tu):
        return False
    return (r["direction"] == "no" and r["entry_price"] is not None
            and 0.20 <= r["entry_price"] < 0.40
            and tu.split("-")[0] not in BLOCKED)

def htc_b(r):
    try:
        h = float(r["htc"])
    except (TypeError, ValueError):
        return "?"
    return "<=12h" if h < 12 else ">12h"

def stats(rs, label):
    n = len(rs)
    if n == 0:
        print(f"{label:44s} n=0")
        return
    w = sum(1 for r in rs if r["prediction_correct"])
    pnl = sum(r["pnl"] or 0 for r in rs)
    pcs = [(r["pnl"] or 0)/max(1, r["contracts"] or 1) for r in rs]
    mean = sum(pcs)/n
    var = sum((x-mean)**2 for x in pcs)/n
    se = math.sqrt(var/n)
    print(f"{label:44s} n={n:4d} win={w/n*100:5.1f}% pnl={pnl:+8.2f} "
          f"per-ct={mean*100:+6.2f}c (SE {se*100:.2f}c, t={mean/se if se else 0:+.2f})")

print("=== htc x zone (all time) ===")
for hb in ("<=12h", ">12h"):
    for z in (True, False):
        stats([r for r in rows if htc_b(r) == hb and in_zone(r) == z],
              f"htc {hb} {'ZONE' if z else 'non-zone'}")
print()
print("=== htc<=12h ZONE by bucket (all time) ===")
for lo, hi in ((0.20, 0.30), (0.30, 0.40)):
    stats([r for r in rows if htc_b(r) == "<=12h" and in_zone(r)
           and lo <= r["entry_price"] < hi], f"zone {int(lo*100)}-{int(hi*100)}c htc<=12h")
print()
print("=== htc<=12h ZONE forward (may25+) ===")
fwd = [r for r in rows if (r["entry_time"] or "")[:10] >= "2026-05-25"]
stats([r for r in fwd if htc_b(r) == "<=12h" and in_zone(r)], "fwd zone htc<=12h")
stats([r for r in fwd if htc_b(r) == "<=12h" and not in_zone(r)], "fwd non-zone htc<=12h")
stats([r for r in fwd if htc_b(r) == ">12h" and in_zone(r)], "fwd zone htc>12h")
print()
print("=== htc<=12h non-zone detail: what is it? (all time) ===")
nz = [r for r in rows if htc_b(r) == "<=12h" and not in_zone(r)]
g = defaultdict(lambda: [0,0,0.0])
for r in nz:
    tu = (r["market_ticker"] or "").upper()
    why = ("thresh" if re.search(r"-T\d", tu) else
           "blocked_city" if tu.split("-")[0] in BLOCKED else
           "yes" if r["direction"] == "yes" else
           f"px{r['entry_price']:.1f}" if r["entry_price"] is not None else "?")
    g[why][0] += 1; g[why][1] += 1 if r["prediction_correct"] else 0; g[why][2] += r["pnl"] or 0
for k, (n, w, pnl) in sorted(g.items(), key=lambda x: -x[1][2]):
    print(f"  {k:14s} n={n:4d} win={w/max(1,n)*100:5.1f}% pnl={pnl:+8.2f}")
print()
print("=== monthly trend of htc<=12h zone trades (entry month) ===")
g = defaultdict(lambda: [0,0,0.0])
for r in rows:
    if htc_b(r) == "<=12h" and in_zone(r):
        m = (r["entry_time"] or "")[:7]
        g[m][0] += 1; g[m][1] += 1 if r["prediction_correct"] else 0; g[m][2] += r["pnl"] or 0
for m in sorted(g):
    n, w, pnl = g[m]
    print(f"  {m}: n={n:3d} win={w/n*100:5.1f}% pnl={pnl:+7.2f}")
