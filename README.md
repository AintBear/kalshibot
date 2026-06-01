# Sibylla — a weather-market trading bot

*A paper-trading bot for [Kalshi](https://kalshi.com)'s binary weather markets. Pulls
forecasts from the National Weather Service and Open-Meteo, scores each bracket
with a Gaussian probability model, applies blockers learned from real settlement
data, and tracks paper P&L over time. **Paper-only by default. Real-money
trading is gated and currently off.***

Built by **AintBear** as a side project — see the [story below](#how-this-project-actually-went).

---

## Where the bot stands today

|                                |                                                          |
| ------------------------------ | -------------------------------------------------------- |
| Mode                           | **Paper only.** Live auto-trading is off and gated.      |
| Brain trust score              | 82 / 100 *(needs ≥ 90 to consider live)*                 |
| Recent-30 expectancy           | **−$0.54 / −0.57¢ CLV** — entry quality gate is **failing** |
| Strategy zone (NO 20–40¢)      | 74.8% on 310 settlements · **+$23.51** realized          |
| Overall realized P&L           | **−$75.92** on 655 real settlements (kept on purpose — see below) |
| Paper trades on record         | 2,603 total · 30 open right now                          |
| Forecast sources               | NWS api.weather.gov + Open-Meteo + AccuWeather (with fallback) |
| Test suite                     | 113 passing                                              |
| Watchdog                       | macOS launchd, restarts backend on health failure        |

**What this means in English.** The bot scans the market, identifies a narrow
slice it has actually learned to trade profitably (NO contracts at 20¢–40¢ on
non-threshold brackets with bad city/segment combos blocked), and enters paper
trades there. Across the historical paper book the bot still has a net loss —
that's because old garbage trades from the learning phase are intentionally
kept in the database so the strategy filter has signal. The slice that the bot
*currently* trades is small-positive in paper. It is **not** profitable enough,
on a long enough forward window, to put real money on. That's why the live
gate is off.

I keep the bad trades in the database on purpose. They're real outcomes from
real forecasts and they're how the bot learned which city + market-type
combinations to stop trading.

---

## What is this thing, in plain English

Kalshi runs binary "yes / no" markets on weather outcomes. *"Will the high
temperature at LAX tomorrow fall between 66 and 68 degrees?"* Yes or no. Each
contract pays $1 if it resolves in your favor, $0 if it doesn't. The market
price is just the crowd's probability estimate compressed into cents — a
contract trading at 30¢ is the crowd saying "30% likely."

Sibylla does five things every fifteen minutes:

1. **Scans** the open Kalshi weather markets for the day.
2. **Fetches forecasts** from the National Weather Service and Open-Meteo (and
   AccuWeather when its key is alive) for each city, averages them, tracks how
   much the sources disagree.
3. **Scores** each bracket — runs a Gaussian probability model around the
   forecasted high or low, returns "what's the chance the actual reading lands
   inside this 2°F bracket."
4. **Filters** — a data-driven blocker layer rejects trades where past data
   says the bot consistently loses (YES contracts, NO sub-20¢, NO over 40¢,
   threshold markets, eight specific city+segment combos, anything within 2°
   of the forecast).
5. **Enters** the survivors as paper trades and tracks P&L until settlement.

The interesting part is layer 4. It's where most of the real engineering went.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          React / Vite UI                         │
│   Dashboard · Alerts · Scanner · Trades · Brain · Settings       │
└────────────────────────────────┬─────────────────────────────────┘
                                 │ JSON over /api
┌────────────────────────────────▼─────────────────────────────────┐
│                       FastAPI backend (8000)                      │
│                                                                  │
│   Scanner ──► Weather Model ──► Position Sizing ──► Auto Entry   │
│   (every    (Gaussian +        (Kelly + blockers)  (paper trade  │
│    15 min)   isotonic calib)                          ledger)    │
│                                                                  │
│   APScheduler: scans · auto-entry · trade lifecycle · learning   │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                          ┌──────▼──────┐
                          │   SQLite    │  trades · alerts · model
                          │  sibylla.db │  outputs · adaptive segs
                          └─────────────┘
```

**Stack**: Python 3.11, FastAPI, APScheduler, httpx, SQLite, Vite, React 18,
Docker Compose. Everything runs in two containers, bound to `127.0.0.1` only.

**Key files** if you're poking around:

- [`backend/app/services/weather_model.py`](backend/app/services/weather_model.py) — the Gaussian scoring + sigma tuning + isotonic calibration
- [`backend/app/services/position_sizing.py`](backend/app/services/position_sizing.py) — the blocker rules, with comments about which combos lose money
- [`backend/app/services/weather_brain.py`](backend/app/services/weather_brain.py) — the "is the bot good enough to trust" score (0–100)
- [`backend/app/services/auto_entry.py`](backend/app/services/auto_entry.py) — paper auto-entry, click-time quote refresh, manual-override blocker enforcement
- [`backend/app/services/scheduler.py`](backend/app/services/scheduler.py) — the always-on loops (scan, settle, learn)
- [`backend/app/routers/health.py`](backend/app/routers/health.py) — `/health` reflects DB + scan freshness + scan error rate
- [`scripts/watchdog.sh`](scripts/watchdog.sh) — macOS launchd watchdog ([install guide](docs/WATCHDOG.md))

---

## Safeguards

The "data-driven" part is mostly about *not* trading. Anything the bot has
historically lost money on is hard-blocked, including from the manual UI:

- **YES contracts at any price** → blocked (27 trades, 0% accuracy)
- **NO under 20¢** → blocked (math requires 80%+ accuracy to break even)
- **NO over 40¢** → blocked (43% accuracy for HIGH, 25% for LOW)
- **Threshold markets (`-T*`)** → blocked (25% NO accuracy)
- **Bracket within 2° of forecast** → blocked (coin flip zone)
- **Eight specific city+segment combos** → blocked (14–60% accuracy)
- **Manual paper buttons** → respect every blocker above; cannot be bypassed

Live trading additionally requires:

- `paper_trading = false` (manual flip in `config/settings.json`)
- Brain trust score ≥ 90
- `entry_quality_ok = true` (recent-window expectancy positive)
- `live_ready = true` from `/api/auto-trade/readiness`

If any of those four are false, the backend refuses to place live orders.

---

## Run it locally

You'll need Docker Desktop (or Colima) installed.

```bash
git clone https://github.com/AintBear/kalshibot.git
cd kalshibot

# Create your own settings file from the template
cp config/settings.example.json config/settings.json
# Edit config/settings.json — at minimum drop in your Kalshi key id
# and put your Kalshi RSA private key at config/kalshi_private_key.pem

docker compose up -d
```

Then open:

- Dashboard → http://localhost:5173
- Backend health → http://localhost:8000/health
- API spec → http://localhost:8000/docs

It boots in paper-trading mode (`paper_trading: true`). It will not place
real orders until the four conditions above are satisfied. Belt and
suspenders.

---

## Keeping the bot always-on

The bot is most useful when it's running 24/7 — it learns from every settled
market, and the scheduler is built for continuous operation. Three honest
options, in order of reliability:

**1. Small cloud VPS** *(recommended for serious use)*. ~$5–10/mo on
Hetzner/DigitalOcean/Fly.io for one tiny instance. Mount `config/` and
`data/` as a persistent volume, run one backend replica (SQLite, single
writer). Survives your laptop sleeping, your power going out, and your home
ISP. The migration is mostly a `fly.toml` or a one-page Compose file.

**2. Windows desktop with WSL2 + Docker Desktop**. Cheaper than a VPS,
better than a Mac for headless work because Windows handles "stay awake
when lid open" cleanly and Task Scheduler is more reliable than launchd for
non-login scenarios. Disable system sleep, enable Docker-on-boot, point Task
Scheduler at `scripts/watchdog.sh` (or its PowerShell equivalent).

**3. macOS with the launchd watchdog** *(what's running today)*. Install
with [`scripts/install-watchdog-launchd.sh`](scripts/install-watchdog-launchd.sh) — see
[`docs/WATCHDOG.md`](docs/WATCHDOG.md). Disable system sleep with `sudo pmset -a sleep 0`.
Caveat: the Mac must be awake, logged in, and Docker/Colima must be running.
A sleeping Mac is a dead bot.

---

## How this project actually went

I want to be honest about the journey because the numbers in the status
table hide it.

I built the first version expecting easy money. The bot took thousands of
trades, lost about $250 across them, and I almost killed the project.

Instead I sat down with the database and started asking *which* trades were
losing. Turns out the bot was right about temperatures most of the time, but
the trade structure was wrong: it was buying NO contracts at 5¢ which
require 85% accuracy just to break even, and it was buying YES on cheap
markets the crowd had basically already settled. Once I started slicing the
historical trades by entry-price band, market type, and city, the picture
clarified:

- **YES on anything**: 0% accuracy on real settlements. Off.
- **NO on anything under 20¢**: ~83% accuracy *but still loses money*
  because the math doesn't work at that price. Off.
- **NO between 20¢ and 40¢**: ~75% accuracy, profitable. The only zone that
  works in the current data.
- **NO over 40¢**: 43% accuracy. Off.

Then I went further and looked at *which cities* the bot was right about.
It turns out the bot is great at Los Angeles highs and San Antonio lows,
and consistently wrong about Denver lows (14% on 7 trades) and Philadelphia
lows (28% on 7). I added eight specific city+segment blockers.

I also caught a few embarrassing bugs along the way:

- A bracket-parsing bug that was treating every 2° bracket as a 1° bracket,
  giving model probabilities that were roughly half what they should be.
- A sigma-bypass bug where the tuned variance parameter was being silently
  overridden by a hardcoded default.
- A retry loop that called `range(1)` instead of `range(3)` and never
  actually retried anything.
- A scheduler race condition that let two scans overlap and create
  duplicate trades — one market lost $12.90 from five duplicate entries
  before I caught it.
- A health endpoint that returned HTTP 200 even when every Kalshi series
  was erroring. For seven days the bot reported "healthy" while doing
  nothing. The fix is what's now in [`backend/app/routers/health.py`](backend/app/routers/health.py)
  — it now factors scan freshness *and* scan error rate, and the launchd
  watchdog restarts the backend on high-error-rate scans (the failure
  mode is a stuck DNS resolver or virtiofs corruption inside the
  long-running uvicorn process; restart is the only fix).
- Manual paper "trade now" buttons used to bypass blockers and use stale
  cached quotes. Both fixed — manual now refuses to fill if it can't get
  a fresh Kalshi bid/ask and respects every recommendation blocker.

The bot is in a much better place than the first version, but it is **not
done**. The "strategy zone" win rate is on a small forward window, and the
recent-30 expectancy is still slightly negative. That's why the brain score
is 82 (not 90), why `entry_quality_ok` is false, and why live trading stays
off.

---

## What I'm working on next

1. **Forward-validate the strategy zone.** The city blockers and the NO
   20–40¢ rule were calibrated on historical data. Need 50+ fresh
   settlements showing recent-30 expectancy holds positive before the live
   gate can open.
2. **Sharpen the calibration layer.** Isotonic calibration is currently
   identity (passthrough) because 82.8% of raw model probabilities sit in
   one 0.1 bucket — there isn't enough spread to fit a useful curve. Need
   to widen the input distribution before re-enabling.
3. **Move off the Mac.** The launchd watchdog works while the Mac is awake.
   A small VPS would be more honest 24/7 infrastructure.
4. **Slice-aware calibration.** Strategy-zone settlements resolve YES at
   ~25%; the global traded sample is ~37%. A single global isotonic curve
   can erase the strategy edge by pulling it toward the global mean.

---

## About this project

I built this on my own time. The trading strategy decisions, the blocker
rules, the debugging hypotheses, and the "what does the data actually say"
analysis are mine. I used Claude (Anthropic's AI assistant) and Codex
heavily for implementation — writing routine code, suggesting refactors,
exploring options for sigma tuning, pair-programming through bugs. I think
of it the way a senior engineer thinks of a sharp intern: it accelerates
the typing, but the judgment is still on me.

The project is also a record of how I work: I keep the bad trades, document
the sessions in [`CLAUDE.md`](CLAUDE.md), and write down what I tried that
didn't work. If you're a recruiter reading this for context, that file is
the honest version of what shipped.

---

## License

MIT. Use whatever you find useful. If you trade with real money based on
something you read here, that's your money and your call. The live gate is
off in this repo for a reason.
