# Sibylla — a quantitative trading bot for weather markets

A full-stack, paper-trading system for [Kalshi](https://kalshi.com)'s binary
weather markets. It pulls forecasts from four independent weather models, prices
each temperature bracket with a calibrated probability model, sizes positions
with Kelly, executes against a real-time market feed, and — most importantly —
**forward-validates its own edge and reports the answer honestly, even when the
answer is "there is no edge."**

> **Paper-only. Real-money trading is gated behind multiple safety layers and is off.**
> This repository is a completed research project, not live financial advice.

---

## Read this first: the result

I built this to answer one question: *can a well-engineered weather model beat
the price of a liquid weather-prediction market?*

After ~700 settled trades and months of forward validation, the honest answer is
**no — and I have the receipts.**

| Finding | Evidence |
| --- | --- |
| **The market prices these outcomes better than my model does.** | Brier score across 734 settlements: **market 0.205 vs model 0.296** — and it holds out-of-sample (forward window: 0.213 vs 0.261). |
| **My model's "confidence" was anti-predictive.** | Trades where the model claimed its *biggest* edge (20¢+) won only **48.1%** and lost **−$30.81**. When it was most sure, it was most wrong. |
| **The one profitable slice decayed to zero forward.** | The "NO 20–40¢" zone was +$25.61 (n=256, 76.2%) before May 25 → **−$13.20** (n=109, 62.4%) in June. |
| **The only surviving edge candidate is tiny and probably noise.** | Entries ≤12h before close: +13.06¢/contract (n=48, t=2.49). Physically plausible (weather is observable near close) but that information is available to the market too, and n=48 after testing dozens of slices is what chance produces. |
| **Overall realized P&L** | **−$92.21** across 734 linked settlements. Kept in the database on purpose — losing trades are training signal. |

**Why I'm proud of a negative result:** the hard part of quantitative trading
isn't building a model that looks good on historical data — anyone can overfit.
The hard part is having the discipline to forward-test it honestly and *not fool
yourself*. This project did exactly that. It found an apparent edge, shipped it,
watched it decay out-of-sample, and shut the live gate rather than rationalizing.
That intellectual honesty is the actual deliverable.

---

## What I built (the engineering)

Even though the strategy didn't beat the market, the system underneath it is
production-grade:

- **Multi-source forecast ensemble** — merges NWS (`api.weather.gov`), AccuWeather,
  Open-Meteo, and ECMWF into a weighted consensus with a source-disagreement
  signal. Graceful degradation when any source fails.
- **Calibrated probability model** — Gaussian CDF over temperature brackets with
  adaptive, time-to-close-scaled sigma; per-(city, market-type) bias correction
  with hard safeguards (min-sample floors, ±0.15 bias clamp) to prevent the
  small-sample calibration blowups that burned an earlier version.
- **Real-time market data** — authenticated Kalshi WebSocket client (RSA-PSS
  request signing, automatic host fallback) capturing sub-second price snapshots
  into a throttled time-series table, enabling *true* closing-line-value
  measurement instead of fabricated numbers.
- **Desk-grade execution engine** — a "work-the-bid" order manager that re-posts
  as it gets outbid, chases within a capped band, crosses near close, and
  reconciles against the broker's position endpoint. Deterministic, idempotent
  order IDs.
- **Risk & safety layer** — hard kill-switch, daily/weekly loss limits with
  automatic revert-to-paper, a pre-trade validation gauntlet on every order, and
  an append-only audit log. Fails closed.
- **Shadow-live mode** — the full live engine runs end-to-end against real quotes
  but simulates fills, so execution can be validated with zero dollars at risk.
- **Ops** — Docker Compose for local dev, a Fly.io deploy path, GitHub Actions
  CI/CD, and self-healing watchdogs for both macOS (launchd) and Windows
  (Task Scheduler).
- **238 passing tests.**

## Tech stack

**Backend:** Python · FastAPI · SQLite · APScheduler · WebSockets · RSA-PSS auth
**Frontend:** React · Vite · Server-Sent Events for live updates
**Infra:** Docker Compose · Fly.io · GitHub Actions

## Architecture

```
Scanner ─▶ Weather model (4-source ensemble + calibration)
             │
             ▼
        Alerts ─▶ Position sizing (Kelly + learned blockers)
                       │
                       ▼
                 Auto-entry ─▶ Trade lifecycle ─▶ Settlement + P&L
                                     │
                                     ▼
                              Brain (learns from outcomes) ─▶ Adaptive policy
```

A real-time layer (WebSocket feed → price snapshots → true CLV) and a risk/
execution layer (order manager, kill-switch, audit log) wrap the whole pipeline.

## Running it locally

```bash
cp config/settings.example.json config/settings.json   # paper-mode defaults, no keys needed
docker compose up -d --build
# backend  → http://localhost:8000/health
# frontend → http://localhost:5173
```

The example config runs in paper mode with no credentials. Live trading requires
Kalshi API keys *and* deliberately flipping multiple off-by-default gates.

## Project status

**Complete / archived.** The research question is answered. The codebase stands
as a demonstration of end-to-end quantitative-system engineering: data
ingestion, modeling, calibration, execution, risk management, real-time
infrastructure, and — the part that matters most — honest forward evaluation.

## License

MIT — see [LICENSE](LICENSE).
