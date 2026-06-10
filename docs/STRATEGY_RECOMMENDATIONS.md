# Strategy Recommendations — Data-Driven Parameter Discovery (WS1)

**Date:** 2026-06-10 (Fable/Windows session 19)
**Data:** `data/sibylla.db` snapshot carried from the Mac (2,734 trades / 21,406 alerts).
**Universe for all stats:** `exit_reason='market_closed'` settlements only, excluding
`paper_reset` (1,420), `bulk_cleanup` (498), and explore-tagged trades (30 settled,
held out per CLAUDE.md rules). That leaves **742 strategy settlements** (734 with
alert linkage for model-vs-market comparisons). Eight trades settled at boot on
2026-06-10 are still `settlement_result=pending` and are excluded from win/P&L stats.
Reproduce every number with the scripts in `scripts/analysis/ws1_*.py` (read-only).

---

## 1. Headline finding: the model does not beat the market

Brier score (lower = better) on all 734 linked settlements, and on the forward
window (entries on/after 2026-05-25, i.e. after the session 15/16 engineering fixes):

| window | n | market price | model_prob | raw_model_prob |
|---|---|---|---|---|
| all | 734 | **0.2051** | 0.2957 | 0.2950 |
| pre-May 25 | 566 | **0.2027** | 0.3059 | 0.3021 |
| forward (May 25+) | 168 | **0.2133** | 0.2612 | 0.2712 |

The market's own price is a materially better forecaster than the bot's model, in
every window, before and after calibration. Worse, **model edge is anti-predictive**:

| claimed model edge | n | win% | P&L |
|---|---|---|---|
| 0–5c | 18 | 83.3% | -$0.33 |
| 5–10c | 125 | 67.2% | -$24.24 |
| 10–20c | 281 | 69.0% | -$36.83 |
| 20c+ | 310 | **48.1%** | **-$30.81** |

The bigger the model thinks its edge is, the worse the outcome. The model's largest
"edges" are its largest miscalibrations. Additionally, the markets the bot selected
settle YES *more often than priced* (e.g. markets priced ~0.30 settled YES 33.0%,
~0.40 settled 43.4%) — the bot has been systematically selling the underpriced side.

**Consequence:** no strategy built on "model_prob vs market price" disagreement
deserves live money today. The pilot universe must come from §3, not from model edge.

## 2. The strategy zone's edge decayed to zero forward

Zone = NO direction, bracket (non-threshold), YES price 20–40c at entry, blocked
cities removed — the slice the live gate has been waiting on.

| entry period | n | win% | P&L | avg CLV* |
|---|---|---|---|---|
| pre-May 25 | 256 | 76.2% | +$25.61 | +4.62c |
| May 25–31 | 54 | 68.5% | -$2.10 | -1.28c |
| Jun 1+ | 109 | 62.4% | -$13.20 | -4.22c |

Forward zone expectancy: **-3.19c/contract (n=155, SE 3.7c)** — statistically zero,
decisively below the +4.6c backtest. Breakeven math confirms: forward 30–40c bucket
won 60.8% vs ~65.7% needed; forward 20–30c won 75.0% vs 76.3% needed. The
retroactive 76.2% "strategy accuracy" never survived forward validation.

*\*See §5 — "CLV" in this DB is per-contract P&L, not true closing-line value.*

## 3. The one slice with real evidence of edge: entry timing

Per-contract P&L by `hours_to_close` at entry (all 734, then forward):

| entries | n | win% | per-contract | forward n | forward per-ct |
|---|---|---|---|---|---|
| ≤12h to close, zone | **48** | **83.3%** | **+13.06c (t=+2.49)** | 37 | +8.73c |
| ≤12h, non-zone | 80 | 28.7% | -4.12c | 5 | -2.40c |
| >12h, zone | 363 | 71.6% | +0.17c | 118 | -6.93c |
| >12h, non-zone | 243 | 49.0% | -17.93c | 106 | -8.12c |

Robustness: positive in both price buckets (20–30c: +13.44c n=25; 30–40c: +12.65c
n=23) and both months (May: +$5.75 n=19; Jun: +$11.08 n=29). Mechanism is physical,
not a data-mining artifact: within ~12h of close, part of the settlement day's
weather is already observable (morning lows, intraday highs), so the model holds
information the market hasn't fully priced. >24h out, the market is simply the
better forecaster and the bot bleeds (-7.32c/ct, n=551).

Honest caveats: n=48 is modest; the Wilson 95% lower bound on the 83.3% win rate
is ~70%, which at a ~70c average NO cost puts the conservative edge near zero.
This slice earns a **capped paper-first pilot**, not size.

### Executed (committed this session)

- **New setting `max_entry_hours_to_close` (default 12.0).** In paper strict mode,
  entries further than 12h from close are soft-blocked (explore mode still samples
  them so the far-entry evidence keeps accumulating). In live mode the gate is
  unconditional, and a missing `hours_to_close` also blocks. `position_sizing.py`,
  `config.py`, `settings.example.json`, tests in `test_entry_window.py` (153/153 pass).

## 4. Calibration audit

- Raw model probability ~0.10 (642 settlements) settles YES **34.7%** of the time.
  The model is severely overconfident in low YES probabilities — which is precisely
  what every NO bet monetizes. This is the root cause of §1.
- The slice calibration shipped in session 16 is now live: 18 (city, market_type)
  slices crossed 20 samples; every one has **positive** bias (+0.08 to +0.41,
  applied bias clamped at +0.15). Direction is right; the clamp cannot fully correct
  a +0.24 average bias, but **do not raise the clamp** — fully correcting to market
  level just reproduces the market price and the alert flow correctly dries up
  (today's scan: 13 alerts, 2 paper trades — the system is appropriately throttled).
- Isotonic stays at identity (84.7% of samples in one bucket) — correct and conservative.

**Recommendation: leave calibration exactly as is.** The fix for §1 is not a bigger
clamp; it is restricting trading to the window where the model has real information (§3).

## 5. "CLV" is mislabeled — it's just P&L

For settlement-riding trades, `trades.clv = entry_yes_price - settlement_value`,
i.e. per-contract P&L restated. The brain's three CLV-flavored components (clv 0/15,
recent_clv 0/10, recent_pnl 0.9/10) are all really expectancy measures. True
closing-line value needs the market price captured just before close — **that data
does not exist in the DB** (no price-path table; `orders` holds only entry orders).

### Recommendation (wired into WS3, the real-time feed)

Capture a quote snapshot per open position per WebSocket tick (throttled), plus a
final pre-close snapshot. Then: real CLV per trade, real stop-loss/take-profit
backtests, and honest brain components. Until then, `stop_loss_pct`/`take_profit_pct`
(both 0.50) cannot be validated against history — the price paths were never stored.
(The 2026-05-08 audit already showed stop-loss exits were negative in every
time-held bucket while ride-to-settlement was better; paper correctly rides.)

## 6. Sizing and proposed pilot caps (owner confirmation required)

Current: `kelly_fraction=0.25`, `max_trade_risk_fraction=0.025`, `max_contracts_per_trade=5`,
`paper_learning_max_contracts=3`. Forward zone-≤12h per-contract stdev is ~40c against a
~+9c mean — a conservative-Kelly bankroll fraction computed at the Wilson lower bound is
≈0 (see §3 caveat), so **data does not support sizing up; it supports a minimum-size pilot**:

| parameter | proposed pilot value | basis |
|---|---|---|
| pilot universe | zone ∩ entries ≤12h to close only | §3, n=48, t=+2.49 |
| max contracts/trade | 2 | Wilson-LB edge ≈ 0 → minimum size while validating |
| max total exposure | $25 | ~30 trades/two weeks × ~0.72 avg cost × 1–2 contracts |
| daily loss limit | $5 (auto-revert to paper) | ~3.5 SE of a 3-trade day |
| weekly loss limit | $15 (auto-revert to paper) | ~2 SE of a 20-trade week at stdev 40c |
| go-live precondition | ≥30 fresh paper settlements under the 12h gate with positive expectancy AND positive true CLV (once WS3 captures it) | forward-validation, not backtest |

These are deliberately tiny: the pilot's job is to validate fills and the 12h-window
edge with real money friction, not to make money yet. **No live order until the owner
confirms these numbers and types GO LIVE.** I also need the live Kalshi balance to
express the loss limits as bankroll fractions.

## 7. Brain gate

The 24 missing points (score 66) are entirely expectancy components: `clv` 0/15,
`recent_clv` 0/10, `recent_pnl` 0.9/10. The gate is doing its job — it is the only
thing that kept this bot paper-only while the zone decayed (§2). **Keep brain ≥ 90 for
full-size live.** For the capped pilot, the §6 precondition (30 gated settlements,
positive expectancy + true CLV) is the data-honest bar; brain-90 would follow
mechanically if the 12h slice performs, because recent windows will fill with gated trades.

## 8. Flagged, not shipped (needs owner sign-off or more data)

1. **Pilot caps in §6** — need owner confirmation + live balance.
2. **Renaming/clarifying the CLV metric in brain UI** — cosmetic but touches scoring
   display; will do alongside WS3's true-CLV capture.
3. **Per-series forward divergence** (KXHIGHTDAL -$8.75 at 28.6% forward vs
   KXHIGHTPHX +$5.22 at 100%) — samples are 4–12 per series; blocking on them now
   would repeat the session-7 overfit. Revisit at ≥20 forward settlements per series.
4. **30–40c bucket** — negative forward overall (-4.9pp vs breakeven) but positive
   within the ≤12h window (+12.65c/ct, n=23). The 12h gate already removes most of
   the bleed; no separate bucket change.
5. **8 pending settlements** from the 2026-06-10 boot (`settlement_result='pending'`,
   JUN09 markets) — settlement backfill skipped them ("skipped: 8"); verify they
   resolve on the next learning refresh; if still pending tomorrow, debug the
   cross-reference job.

## 9. Guardrails honored

No historical trades deleted or mutated (analysis is read-only; scripts use
`mode=ro`). Explore trades excluded from all edge stats. Minimum-sample rules
applied (no action taken on any slice under ~20 settlements; the one shipped change
rests on n=48/551/734 with t-stats reported). Calibration clamps untouched.
