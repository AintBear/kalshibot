"""WS1: data-driven parameter discovery. Read-only against sibylla.db.

All stats on real settlements only (exit_reason='market_closed'), excluding
paper_reset/bulk_cleanup always, and excluding explore trades unless noted.
Trades store YES-price coordinates; the '20-40c' strategy zone refers to the
YES price at entry for NO-direction trades.
"""
import json
import sqlite3
from collections import defaultdict

con = sqlite3.connect("file:/app/data/sibylla.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

BASE = """
    FROM trades t
    LEFT JOIN alerts a ON a.id = t.alert_id
    WHERE t.exit_reason = 'market_closed'
      AND COALESCE(json_extract(a.details, '$.learning_mode'), '') != 'explore'
"""

def q(sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()

def bucket(yes_price):
    if yes_price is None:
        return "?"
    p = float(yes_price)
    for lo, hi in ((0, .10), (.10, .20), (.20, .30), (.30, .40), (.40, .50),
                   (.50, .70), (.70, .85), (.85, 1.01)):
        if lo <= p < hi:
            return f"{int(lo*100):02d}-{int(hi*100) if hi <= 1 else 100}c"
    return "?"

def series_of(ticker):
    return (ticker or "").upper().split("-")[0]

def mtype(ticker):
    tu = (ticker or "").upper()
    import re
    thresh = bool(re.search(r"-T\d", tu))
    kind = "HIGH" if "HIGH" in tu else ("LOW" if "LOW" in tu else "OTHER")
    return kind, ("threshold" if thresh else "bracket")

rows = q(f"""
    SELECT t.id, t.market_ticker, t.direction, t.entry_price, t.exit_price,
           t.clv, t.contracts, t.pnl, t.prediction_correct, t.entry_time,
           t.exit_time, t.settlement_result
    {BASE}
""")
print(f"=== STRATEGY SETTLEMENTS (market_closed, non-explore): {len(rows)} ===")

explore_rows = q("""
    SELECT COUNT(*) n, SUM(t.pnl) pnl,
           AVG(CASE WHEN t.prediction_correct THEN 1.0 ELSE 0.0 END) acc
    FROM trades t LEFT JOIN alerts a ON a.id = t.alert_id
    WHERE t.exit_reason = 'market_closed'
      AND COALESCE(json_extract(a.details, '$.learning_mode'), '') = 'explore'
""")
e = explore_rows[0]
print(f"(held-out explore settlements: {e['n']}, pnl {e['pnl'] or 0:+.2f}, acc {(e['acc'] or 0)*100:.1f}%)\n")

# ---- aggregate helper ----
def agg(keyfn, rows, min_n=0, sort_by="pnl"):
    g = defaultdict(lambda: dict(n=0, wins=0, pnl=0.0, clv_sum=0.0, clv_n=0))
    for r in rows:
        k = keyfn(r)
        if k is None:
            continue
        d = g[k]
        d["n"] += 1
        d["wins"] += 1 if r["prediction_correct"] else 0
        d["pnl"] += r["pnl"] or 0.0
        if r["clv"] is not None:
            d["clv_sum"] += r["clv"]
            d["clv_n"] += 1
    out = []
    for k, d in g.items():
        if d["n"] < min_n:
            continue
        out.append((k, d["n"], d["wins"]/d["n"]*100, d["pnl"],
                    (d["clv_sum"]/d["clv_n"]*100) if d["clv_n"] else None))
    out.sort(key=lambda x: -x[3] if sort_by == "pnl" else -x[1])
    return out

def show(title, data, limit=None):
    print(f"--- {title} ---")
    print(f"{'slice':38s} {'n':>5s} {'win%':>6s} {'P&L':>9s} {'avgCLV':>7s}")
    for k, n, w, pnl, clv in (data[:limit] if limit else data):
        clv_s = f"{clv:+.2f}c" if clv is not None else "  n/a"
        print(f"{str(k):38s} {n:5d} {w:6.1f} {pnl:+9.2f} {clv_s:>7s}")
    print()

# 1. By (direction, entry bucket)  — entry_price is YES coords
show("direction x YES-price bucket", agg(
    lambda r: (r["direction"], bucket(r["entry_price"])), rows, min_n=5))

# 2. The strategy zone vs everything else
def in_zone(r):
    k, t = mtype(r["market_ticker"])
    return (r["direction"] == "no" and t == "bracket"
            and r["entry_price"] is not None and 0.20 <= r["entry_price"] < 0.40
            and series_of(r["market_ticker"]) not in {
                "KXLOWTDC","KXLOWTDEN","KXLOWTPHIL","KXLOWTOKC","KXHIGHTNOLA",
                "KXHIGHTSATX","KXLOWTDAL","KXLOWTNOLA","KXHIGHTSFO"})
show("strategy zone vs rest", agg(lambda r: "ZONE" if in_zone(r) else "rest", rows))

# 3. Zone by series (city)
show("strategy-zone trades by series", agg(
    lambda r: series_of(r["market_ticker"]) if in_zone(r) else None, rows, min_n=3))

# 4. Forward validation: by entry month/period
def period(r):
    et = (r["entry_time"] or "")[:10]
    if et >= "2026-06-01":
        return "C_jun01+"
    if et >= "2026-05-25":
        return "B_may25-31"
    return "A_pre-may25"
show("ALL strategy trades by entry period", agg(period, rows))
show("ZONE trades by entry period", agg(
    lambda r: period(r) if in_zone(r) else None, rows))

# 5. Recent 30/60 settlements (by exit_time) — what the brain sees
recent = sorted(rows, key=lambda r: r["exit_time"] or "", reverse=True)
for k in (30, 60, 117):
    rs = recent[:k]
    wins = sum(1 for r in rs if r["prediction_correct"])
    pnl = sum(r["pnl"] or 0 for r in rs)
    clvs = [r["clv"] for r in rs if r["clv"] is not None]
    clv = sum(clvs)/len(clvs)*100 if clvs else 0
    zone_n = sum(1 for r in rs if in_zone(r))
    print(f"recent-{k}: win {wins}/{len(rs)} ({wins/max(1,len(rs))*100:.0f}%), "
          f"pnl {pnl:+.2f}, avg CLV {clv:+.2f}c, zone trades {zone_n}")
print()

# 6. Recent zone losers detail (last 20 zone trades)
zone_recent = [r for r in recent if in_zone(r)][:20]
print("--- last 20 ZONE settlements (newest first) ---")
for r in zone_recent:
    clv_s = f"{r['clv']*100:+.1f}c" if r["clv"] is not None else "n/a"
    print(f"{(r['exit_time'] or '')[:10]} {r['market_ticker']:28s} yes@{r['entry_price']:.2f} "
          f"{'WIN ' if r['prediction_correct'] else 'LOSS'} pnl {r['pnl'] or 0:+.2f} clv {clv_s}")
print()

# 7. Calibration: model_prob vs settlement by raw prob bucket (zone + all)
cal = q(f"""
    SELECT json_extract(a.details, '$.raw_model_prob') raw_p,
           a.model_prob, t.direction, t.settlement_result, t.prediction_correct,
           t.market_ticker, t.entry_price
    {BASE} AND a.id IS NOT NULL
""")
g = defaultdict(lambda: [0, 0, 0.0])
for r in cal:
    rp = r["raw_p"] if r["raw_p"] is not None else r["model_prob"]
    if rp is None:
        continue
    b = round(float(rp), 1)
    yes_won = 1 if (r["settlement_result"] in ("yes", 1, "1", True)) else 0
    g[b][0] += 1
    g[b][1] += yes_won
    g[b][2] += float(rp)
print("--- calibration: raw model prob vs YES settlement rate ---")
for b in sorted(g):
    n, wins, s = g[b]
    print(f"raw~{b:.1f}: n={n:4d}  avg_raw={s/n:.3f}  actual_yes_rate={wins/n:.3f}")
print()

# 8. model_calibration table now
print("--- model_calibration slices (n>=20) ---")
for r in q("""SELECT city, market_type, sample_count, calibration_bias,
                     avg_model_prob, avg_settlement_rate
              FROM model_calibration WHERE sample_count >= 20
              ORDER BY ABS(calibration_bias) DESC"""):
    print(f"{r['city']:8s} {r['market_type']:6s} n={r['sample_count']:3d} "
          f"bias={r['calibration_bias']:+.3f} model={r['avg_model_prob']:.3f} "
          f"actual={r['avg_settlement_rate']:.3f}")
print()

# 9. Orders/price-path data availability for stop/TP backtest
for t in ("orders", "model_outputs", "forecast_snapshots", "balance_snapshots"):
    n = q(f"SELECT COUNT(*) c FROM {t}")[0]["c"]
    print(f"{t}: {n} rows")
cols = q("PRAGMA table_info(orders)")
print("orders cols:", [c["name"] for c in cols])
n_marks = q("""SELECT COUNT(*) c FROM trades
               WHERE exit_reason='market_closed' AND clv IS NOT NULL""")[0]["c"]
print(f"settled trades with CLV recorded: {n_marks}")
