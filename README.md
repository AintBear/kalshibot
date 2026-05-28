# Sibylla Weather Bot

Sibylla is now a weather-only Kalshi trading assistant. It scans climate and
weather markets, ingests NOAA/NWS weather data, generates weather model outputs,
surfaces alerts, records paper/live order lifecycle state, and tracks weather
trade performance.

Legacy sports, politics, economics, polling, FedWatch, Polymarket, and odds
modules have been removed from startup and purged from the active database.

## Current Runtime

- Backend: FastAPI on port `8000`
- Frontend: Vite/React on port `5173`
- Database: SQLite at `data/sibylla.db`
- Active category: `weather`
- Default trading mode: paper trading

## Start Locally

```powershell
docker compose build
docker compose up -d
docker compose logs -f backend
```

Open:

- Backend health: `http://localhost:8000/health`
- Dashboard: `http://localhost:5173`

## Verification

Run the core backend checks in Docker:

```powershell
docker compose run --rm --no-deps backend python -m unittest discover -s tests
```

Run the frontend production build:

```powershell
docker compose run --rm --no-deps frontend npm run build -- --outDir /tmp/sibylla-vite-build --emptyOutDir
```

Check the active DB:

```powershell
@'
import sqlite3
con = sqlite3.connect("data/sibylla.db")
print(con.execute("PRAGMA quick_check").fetchone()[0])
for table in ("markets", "alerts", "model_outputs", "trades", "orders"):
    print(table, con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
con.close()
'@ | python -
```

## Cloud Notes

Use one backend replica while SQLite is the database. Mount persistent storage to
`/app/data` and mount config/secrets to `/app/config`.

Required environment:

```text
ENVIRONMENT=production
DATABASE_URL=sqlite:////app/data/sibylla.db
DB_PATH=/app/data/sibylla.db
CONFIG_PATH=/app/config/settings.json
KALSHI_KEY_ID=...
KALSHI_PRIVATE_KEY_PATH=/app/config/kalshi_private_key.pem
```

Optional:

```text
ACCUWEATHER_API_KEY=...
```

Keep `paper_trading=true` until health checks, data freshness, alerts, and
execution tests are green.

## Guardrails

- Startup runs a weather-only DB cleanup before migrations.
- Empty malformed core tables are rebuilt.
- Legacy non-weather tables are dropped.
- Existing non-weather rows in active runtime tables are purged.
- The scanner ignores non-weather markets before insert/update.
- Tests cover weather-only service scope, scanner categorization, migration
  cleanup, and execution lifecycle.
