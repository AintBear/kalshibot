"""WS5: weather pipeline freshness check on the latest scan's alerts."""
import json
import sqlite3

con = sqlite3.connect("file:/app/data/sibylla.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = con.execute(
    """SELECT market_ticker, updated_at, details FROM alerts
        WHERE status='pending' ORDER BY updated_at DESC LIMIT 8"""
).fetchall()

for r in rows:
    d = json.loads(r["details"] or "{}")
    forecast = d.get("forecast") or {}
    intraday = d.get("intraday_observation") or {}
    print(f"{r['market_ticker']:32s} updated={r['updated_at']}")
    print(f"  sources={d.get('forecast_sources')}")
    print(f"  forecast high={forecast.get('high')} low={forecast.get('low')} "
          f"src_disagreement={forecast.get('source_disagreement')}")
    if intraday:
        print(f"  intraday: observed_high={intraday.get('observed_high_so_far')} "
              f"observed_low={intraday.get('observed_low_so_far')} "
              f"current={intraday.get('current_temp')} hour={intraday.get('local_hour')}")
    else:
        print("  intraday: none")
    print(f"  raw_model_prob={d.get('raw_model_prob')} hours_to_close={d.get('hours_to_close')}")
