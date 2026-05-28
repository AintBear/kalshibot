# Sibylla — a weather-market trading bot

*A paper-trading bot for [Kalshi](https://kalshi.com)'s binary weather markets. Pulls
forecasts from the National Weather Service and Open-Meteo, scores each bracket
with a Gaussian probability model, applies blockers learned from real settlement
data, and tracks paper P&L over time.*

Built by **AintBear** as a side project — see the [story below](#how-this-project-actually-went).

---

## Where the bot stands today

|                          |                                                          |
| ------------------------ | -------------------------------------------------------- |
| Prediction accuracy      | **77.1%** on the strategy-filtered window (240 settled trades) |
| Strategy P&L             | **+$30.79** retroactive, awaiting live-validation        |
| Overall paper P&L        | **−$225.03** (includes the bad early period — kept on purpose, see below) |
| Settled trades on record | **566**                                                  |
| Open paper trades        | 0 (entering only when blockers + edge calc both clear)   |
| Forecast sources         | NWS api.weather.gov + Open-Meteo (free, no key)          |
| Test suite               | 101 passing                                              |

I keep the bad trades in the database on purpose. They're real outcomes from real
forecasts and they're how the bot learns which city + market-type combinations to
stop trading.

---

## What is this thing, in plain English

Kalshi runs binary "yes / no" markets on weather outcomes. *"Will the high
temperature at LAX tomorrow fall between 66 and 68 degrees?"* Yes or no. Each
contract pays $1 if it resolves in your favor, $0 if it doesn't. The market
price is just the crowd's probability estimate compressed into cents — a
contract trading at 30¢ is the crowd saying "30% likely."

Sibylla does five things every fifteen minutes:

1. **Scans** the open Kalshi weather markets for the day.
2. **Fetches forecasts** from the National Weather Service and Open-Meteo for
   each city, averages them, tracks how much the two sources disagree.
3. **Scores** each bracket — runs a Gaussian probability model around the
   forecasted high or low, returns "what's the chance the actual reading lands
   inside this 2°F bracket."
4. **Filters** — a data-driven blocker layer rejects trades where past data says
   the bot consistently loses (specific cities, specific entry-price bands,
   threshold markets, anything within 2° of the forecast).
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
Docker Compose. Everything runs in two containers.

**Key files** if you're poking around:

- `backend/app/services/weather_model.py` — the Gaussian scoring + sigma tuning
- `backend/app/services/position_sizing.py` — the blocker rules, the part with
  all the comments about which combos lose money
- `backend/app/services/weather_brain.py` — the "is the bot good enough to
  trust" score (0–100)
- `backend/app/services/scheduler.py` — the always-on loops

---

## Run it locally

You'll need Docker Desktop installed.

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

It boots in paper-trading mode (`paper_trading: true`). It will not place real
orders until you flip that flag *and* the brain trust score crosses 90 *and* the
strategy blockers are passing forward-validation. Belt and suspenders.

---

## Keeping the bot always-on

The bot is most useful when it's running 24/7 — it learns from every settled
market, and the scheduler is built for continuous operation. Three ways to keep
it up.

**Home PC** *(what I do right now)*. Cheapest, simplest. Set the machine to
never sleep on AC power, enable "start up automatically after a power failure"
in your OS settings, and tell Docker to start on boot. A modern PC idles at
30–60 watts — somewhere between $3 and $7 a month on electricity, give or take.

**Fly.io / Railway / Render** *(roadmap)*. Free tier covers a bot this size.
Logs accessible from your phone, auto-restarts on crash, no electricity bill.
The migration is mostly a `fly.toml` + persistent volume for the SQLite file.
I'll move it once the strategy has a few more weeks of forward-validation under
its belt.

**Raspberry Pi 5** *(the cheap long-term answer)*. ~$80 one-time, <5 watts
idle. Pays for itself vs the cloud in about a year. Tempting, but the PC works
for now.

---

## How this project actually went

I want to be honest about the journey because the numbers in the status table
hide it.

I built the first version expecting easy money. The bot took thousands of
trades, lost about $250 across them, and I almost killed the project.

Instead I sat down with the database and started asking *which* trades were
losing. Turns out the bot was right about temperatures most of the time, but
the trade structure was wrong: it was buying NO contracts at 5¢ which require
85% accuracy just to break even, and it was buying YES on cheap markets the
crowd had basically already settled. Once I started slicing the historical
trades by entry-price band, market type, and city, the picture clarified:

- **YES on anything under 20¢**: 5% accuracy. Off.
- **NO on anything under 20¢**: ~83% accuracy *but still loses money* because
  the math doesn't work at that price. Off.
- **NO between 20¢ and 40¢**: 77% accuracy, profitable. The only zone that
  works.
- **NO over 40¢**: 43% accuracy. Off.

Then I went further and looked at *which cities* the bot was right about. It
turns out the bot is great at Los Angeles highs (90% on 10 trades) and San
Antonio lows (100% on 10 trades), and consistently wrong about Denver lows
(14% on 7 trades) and Philadelphia lows (28% on 7). I added eight specific
city+segment blockers. Retroactively, that flips the strategy from −$26.90 to
+$30.79 across 240 trades.

I also caught a few embarrassing bugs along the way:

- A bracket-parsing bug that was treating every 2° bracket as a 1° bracket,
  giving the model probabilities that were roughly half what they should be.
- A sigma-bypass bug where the tuned variance parameter was being silently
  overridden by a hardcoded default.
- A retry loop that called `range(1)` instead of `range(3)` and never actually
  retried anything.
- A scheduler race condition that let two scans overlap and create duplicate
  trades — one market lost $12.90 from five duplicate entries before I caught
  it.

And then, the one that motivated this whole cleanup: the bot was up in Docker
for a week, reporting "healthy," and producing zero new trades. The root cause
was a stuck DNS resolver inside the long-running Python process — fresh
`python` shells in the same container could resolve `api.weather.gov` fine,
but the uvicorn worker had cached negative results from an earlier network
blip and was failing every forecast fetch. `docker compose restart backend`
fixed it instantly. The fix is a one-liner; the lesson is that "container
healthy" is not the same as "doing its job."

The bot is in a good place now, but it's not done. The city blockers are based
on small samples (7–15 trades each) and need 50+ fresh settlements before I can
say with confidence they hold forward.

---

## What I'm working on next

1. **Forward-validate the city blockers.** Backtest improvement is +$57 across
   240 trades. Need to see if that survives a fresh window.
2. **Decide on sigma.** Code currently runs with σ=3.5 (HIGH) / σ=3.0 (LOW).
   The CLAUDE.md history says it should be 9.0 / 8.0. Either the doc is stale
   or the constants regressed. Want to A/B this carefully.
3. **Move to cloud.** Fly.io or Render once the forward window looks good.
4. **Sharpen the calibration layer.** Isotonic calibration is wired up but the
   model output distribution is bimodal — 51% of model probabilities sit at
   0.1. There's room to widen the input distribution before calibrating.

---

## About this project

I built this on my own time. The trading strategy decisions, the blocker rules,
the debugging hypotheses, and the "what does the data actually say" analysis
are mine. I used Claude (Anthropic's AI assistant) heavily for implementation —
writing routine code, suggesting refactors, exploring options for sigma tuning,
pair-programming through bugs. I think of it the way a senior engineer thinks
of a sharp intern: it accelerates the typing, but the judgment is still on me.

The project is also a record of how I work: I keep the bad trades, document
the sessions in `CLAUDE.md`, and write down what I tried that didn't work. If
you're a recruiter reading this for context, that file is the honest version
of what shipped.

---

## License

MIT. Use whatever you find useful. If you trade with real money based on
something you read here, that's your money and your call.
