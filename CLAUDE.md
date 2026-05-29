# KalshiBot - Project Instructions

## Bot Status (updated 2026-05-29, session 12 — verified from DB/runtime)

**Overall realized P&L: -$76.36** on 567 real `market_closed` settlements | **Strategy zone P&L: +$25.61** on 256 NO 20-40c bracket trades (blocked cities + explore excluded) | **80 open paper trades** (50 strategy, 30 legacy explore) | **Brain score: 71**

### What's actually true

- The bot is running from `/Users/AintBear/Projects/kalshibot` with a launchd watchdog installed. Latest verified scan processed 539/539 markets with 0 series errors. Watchdog last exit code was 0 and live auto remains off.
- New paper entries are strict-strategy only. `paper_learning_explore_enabled` is now false in the local runtime and in `config/settings.example.json`; the 30 open explore trades are legacy held-out diagnostics and should not affect strategy learning.
- The "77.1% strategy accuracy" figure carried forward across sessions 7/8/9 was a **retroactive** simulation — it filtered historical trades by what *would* have been allowed under the new blocker rules. Forward validation on those rules never happened because the bot stopped trading right after they shipped.
- Real DB numbers (excluding `paper_reset`, `bulk_cleanup`, and explore from strategy stats): 567 market-closed settlements, -$76.36 realized P&L overall. The code-aligned strategy zone (NO 20-40c bracket trades, blocked cities removed including KXLOWTDEN, non-threshold) is the only consistently profitable slice: 256 trades, 76.2% accuracy, +$25.61 P&L.

### What changed this session

Session 10 added **explore-mode learning** (`paper_learning_explore_enabled`, initially 3 trades/run): the bot takes 1-contract paper bets on candidates blocked by *soft* blockers — threshold markets, NO 40c+, blocked city+segment, bracket within 1° of forecast — while still respecting the **iron-law blockers** (YES at all, NO sub-20c, NO 85c+). Session 11 capped explore at 30 open trades and excluded explore outcomes from strategy learning until reviewed. Session 12 disabled new explore entries so the active bot runs the strict strategy only.

Session 11 also stopped the dynamic isotonic rebuild from overwriting identity calibration with the current concentrated bucket data. The running backend had been rebuilding 8 non-monotonic/sparse knots, which pushed many raw 0.08-0.12 probabilities to about 0.37 and helped create an overwhelmingly YES alert universe. Startup now leaves isotonic at identity until coverage is broad enough.

Session 12 added a macOS launchd watchdog. It starts Docker Compose if down, restarts backend on failed health, triggers a scan if stale/missing, and pokes paper auto-entry only when paper auto is enabled and ready. It never enables live trading.

| Gate                        | Current (verified)         | Target     | Status |
|-----------------------------|----------------------------|------------|--------|
| Strategy zone win rate      | 76.2% (256 settlements)    | >= 70%     | PASS   |
| Strategy zone P&L           | +$25.61                    | >= $0      | PASS   |
| Overall realized P&L        | -$76.36 (567 settlements)  | >= $0      | FAIL   |
| Bot entering new trades     | yes (50 strategy + 30 legacy explore open; new explore disabled) | yes | PASS |
| Brain trust score           | 71                         | >= 90      | FAIL   |
| Kalshi credentials          | configured                 | configured | PASS   |
| Auto-eligible segments      | 5 (paper)                  | >= 1       | PASS   |
| Forecast sources            | NWS + Open-Meteo + AccuW   | working    | PASS   |

## Daily Improvement System

Each session, do exactly two things from this priority list (top = highest impact):

1. **Forward-validate open strategy and legacy explore trades separately** — 50 strategy trades and 30 legacy explore trades are now open. Wait for settlement before relaxing blockers or re-enabling explore.
2. **Keep isotonic conservative** — current clean data has 564 usable bucketed samples but 82.8% sit in one 0.1 bucket, so identity is safer than a global correction. Revisit only after broader raw-probability coverage.
3. **Inspect calibration by market slice, not global only** — the strategy zone is true YES 25.4% against avg market 29.9%, while the global traded sample is true YES 37.4%. A global calibration can erase the edge.
4. **Keep strict mode on unless explore proves useful** — `paper_learning_explore_enabled=false` now. Re-enable only if the current 30 held-out explore trades settle cleanly.
5. **Infrastructure** — Tests, monitoring, deployment reliability.

After each session, update the status table above and note what changed.

## What Was Done (2026-05-29, session 12 — Codex)

- **Installed a local watchdog outside macOS protected folders.** LaunchAgent failed from `~/Downloads` with exit code 126 / `Operation not permitted`, so runtime was copied to `/Users/AintBear/Projects/kalshibot` and the LaunchAgent now points there. `launchctl` shows last exit code 0.
- **Added `scripts/watchdog.sh` and `scripts/install-watchdog-launchd.sh`.** The watchdog runs every 5 minutes, starts Compose, restarts backend on failed `/health`, handles stale/stuck scans, and calls `/api/auto-trade/run` for paper only after a completed scan when backend readiness says it is safe. It does not enable live trading.
- **Prevented overlapping automation cycles.** `auto_entry.run_automation_cycle()` now uses a process-local nonblocking lock and returns a skipped result if another cycle is already running.
- **Made strict mode the active runtime posture.** `paper_learning_explore_enabled=false` in local settings and the example settings. The backend settings API now allows explore toggles so Claude/UI can expose them cleanly later. Existing explore trades remain tagged and held out from strategy learning.
- **Verified.** Backend health OK, scan status complete 539/539 with 0 series errors, auto status paper-ready/live-off, watchdog run completed with 0 new entries because no eligible strict candidates remained, and backend tests passed (`108 passed`).

## What Was Done (2026-05-29, session 10)

- **Diagnosed: bot was alive but blocked from trading.** Scheduler running fine (scans 15min, auto-entry 5min, lifecycle 5min). Manual `/api/auto-trade/run` returned `candidates_considered=376, eligible_candidates=0` on every cycle. Of 200 pending alerts, 189 were YES (correctly iron-law blocked) and 11 were NO with positive NO-side edge but **all rejected by soft blockers** (threshold markets, NO 40c+, blocked city+segment, bracket within 2°). Net: bot has been trade-less since May 21.
- **Found the CLAUDE.md status table was overstated.** Verified DB shows 567 real `market_closed` settlements (matches "566 real settlements" line), -$76.36 realized P&L (vs claimed -$225.03 which counted bulk_cleanup losses), and the "77.1% accuracy / 240 trades" strategy line was a retroactive simulation never forward-validated. Real strategy-zone (NO 20-40c bracket, blocked cities removed, non-threshold) is 274 trades / 74.1% accuracy / +$17.25 — solid but smaller than the headline number suggested.
- **Refactored `position_sizing.recommend_alert` blocker logic** into iron-law (always-on) vs soft (evidence-based) layers. Added `explore: bool = False` parameter. Iron-law in paper mode: YES at all, NO sub-20c, NO 85c+ (all three confirmed catastrophic on real settlements). Soft blockers (threshold, NO 40c+, blocked-city, segment performance, bracket within 1° of forecast) are suppressed in explore mode.
- **Tightened `bracket within 2° of forecast` → `bracket within 1° of forecast`** in the soft-blocker tier. The 2° rule was excluding too many borderline-but-tradeable bracket markets.
- **Added explore second-pass to `auto_entry.auto_enter_qualifying_alerts`.** After the normal entry loop finishes, if `paper_learning_explore_enabled` is true, re-scans rejected candidates with `explore=True`, ranks by `side_edge`, and enters up to `paper_learning_explore_max_per_scan` (default 3) at 1 contract each. Each explore trade is tagged `learning_mode='explore'` in the alert details for downstream filtering.
- **Added settings** `paper_learning_explore_enabled: true` and `paper_learning_explore_max_per_scan: 3` to `config/settings.json`.
- **Verified**: post-restart, three sequential `/api/auto-trade/run` calls each placed 3 explore trades. 9 paper trades now open (was 0 before the change), all NO bets on bracket/threshold markets with side_edge +5c to +40c. All 101 tests still pass.
- **KXRAIN warnings investigated** — not a bug. `KXRAIN*M` are monthly accumulation markets correctly skipped by `_requires_accumulation_model()` in `_estimate_model_prob()`. The log line is just noise.
- **Known follow-up (not done this session):** `weather_brain.py` segment-stats queries should exclude `learning_mode='explore'` so explore losses don't degrade strategy-mode segment scores. Listed as priority #2 above.

## What Was Done (2026-05-29, session 11 — Codex)

- **Did not commit session 10 as-is.** I agreed with the iron-law / soft-block split in principle, but found three issues that needed correction first: strategy stats were not excluding blocked city segments, `KXLOWTDEN` was documented as blocked but missing from code, and explore mode could keep filling the book because the live `max_open_paper_trades` cap is 500.
- **Verified Claude's strategy-zone claim against the DB and corrected the numbers.** With the actual code blocker list before my fix: 264 trades, 74.6% accuracy, +$17.45. After adding the data-backed `KXLOWTDEN` blocker: 256 trades, 76.2% accuracy, +$25.61. The broader unblocked NO 20-40c non-threshold slice is only 345 trades, 67.8%, -$30.00, so the blocked-city filter materially matters.
- **Pushed back on the calibration diagnosis.** The source file default was identity, but the running backend had rebuilt 8 isotonic knots from concentrated buckets. Current clean bucket coverage is 564 samples across 7 usable buckets, but 82.8% are in the 0.1 bucket. That sparse global rebuild was pushing raw 0.08-0.12 probabilities toward ~0.37 and contributing to an overly YES-heavy alert universe. I changed the rebuild to require broader coverage, use monotonic PAVA when coverage is adequate, and otherwise leave identity in place.
- **Separated explore from strategy learning.** `weather_brain.py` now excludes `learning_mode='explore'` from strategy learning samples, CLV, P&L, recent windows, and prediction accuracy, while returning separate `explore_stats`. `adaptive_policy.rebuild_snapshots()` now keeps normal segment snapshots strategy-only and writes `explore:*` snapshots as held-out, non-auto-eligible diagnostics.
- **Capped explore mode.** Added defaults and example settings for `paper_learning_explore_max_open=30`; auto-entry now reports explore open/cap and will not exceed that cap. After restart and scan, open paper book is 60 total: 30 strict strategy trades and 30 explore trades.
- **Verified runtime after changes.** Full backend tests pass (`107 passed`). Restarted backend. `/health` is OK. Startup calibration logged `updated=False`, `reason='concentrated_bucket_coverage'`. Fresh scan completed 539/539 markets, 69 alerts, 0 series errors, AccuWeather live. Pending alerts after scan: 314 total, 119 NO, 195 YES, with 25 strict-zone NO candidates still pending after 30 strategy entries.
- **What Claude should scrutinize next:** whether identity calibration plus market anchor is still too extreme in specific slices, and whether the 30 newly opened strict strategy trades actually settle near the historical 76% rate. Do not increase explore until the current 30 explore trades settle.

## What Was Done (2026-05-29, session 9)

- **Root-caused second outage in 24h**: Colima VirtioFS on macOS 26 (Tahoe beta) lost inode handles on the `/app/data` bind mount — `open('/app/data/sibylla.db')` returned EPERM while `stat()` succeeded and new file creation worked. Same class of bug as session 8's DNS freeze, different layer. `colima stop && colima start && docker compose up -d` restored DB access. 320+ "unable to open database file" errors had been silently accumulating for ~24h.
- **Fixed health-check masking** (`backend/app/routers/health.py`): endpoint returned HTTP 200 even when `issues=["database_unavailable"]`, so Docker's `curl -f` healthcheck reported "healthy" through the entire outage. Now returns 503 when issues list is non-empty. This is what made the session 8 + session 9 outages invisible.
- **Removed `--reload` from production** (`backend/Dockerfile:14`): WatchFiles reloader was spawning spurious restarts on virtiofs inotify noise. Kept stock uvicorn command.
- **Wired up INFO-level logging** (`backend/app/main.py`): added `logging.basicConfig(level=logging.INFO)` so scheduler `logger.info()` calls actually surface in `docker logs`. Previously only `logger.error()` was visible.
- **Made `_automation_enabled()` failures loud** (`backend/app/services/scheduler.py:25-31`): was swallowing all config-load exceptions and silently returning False, causing scans to skip with zero log output. Now logs at ERROR before returning.
- **Reverted sigma regression** (`backend/app/services/weather_model.py:1236,1240`): HIGH 3.5→9.0, LOW 3.0→8.0. The initial git commit shipped the regressed values; CLAUDE.md sessions 2 and 6 had documented the correct 9.0/8.0 but the working tree was never pushed until session 8 created the initial commit. Effective sigma now back to documented 6.3–9.9 (HIGH) and 5.6–8.8 (LOW).
- **Settled stuck trade 2485** (KXHIGHTPHX-26MAY28-B95.5): YES, entry 0.08, settled NO, -$0.08. Was stuck because DB was unreachable; cleared automatically by startup settlement job after the Colima restart.
- **Switched backend restart policy** (`docker-compose.yml`): `unless-stopped` → `on-failure:3`. Combined with the 503 health fix, this enables fail-fast on future virtiofs corruption.
- **Secrets audit: CLEAN**. `git log -p` across all secret paths returns 0 lines. `settings.json`, `*.pem`, `.env`, `data/`, `.claude/`, HANDOFF/CODEX files all correctly gitignored from the first commit. README endorses CLAUDE.md publicly on purpose — left as-is.
- **All 101 tests still pass**.

## What Was Done (2026-05-28, session 8)

- **Root-caused 7-day silent outage**: bot was up in Docker (healthy 2 days) but produced ZERO new trades from May 21 through May 28. Cause: long-running uvicorn process had cached negative DNS results — `getaddrinfo` returned "Name or service not known" for `api.weather.gov` and `api.open-meteo.com` on every scan. Fresh Python execs inside the same container resolved cleanly; only the process-resident resolver was stuck. Fix: `docker compose restart backend`. Now producing 364 active alerts; NWS + Open-Meteo both responding (source_disagreement ~2°F).
- **Forward validation of session-7 city blockers still pending** — cannot validate without new entries. 5 strategy-zone alerts are live right now, all with negative edge — the blockers + edge calc are correctly refusing low-quality trades. Wait for settlement window before drawing conclusions.
- **Pre-GitHub hardening**:
  - Hardened `.gitignore`: explicitly excludes `config/settings.json` (live Kalshi `key_id` was previously NOT covered by any rule), `config/kalshi_private_key.pem` (defense-in-depth on top of `*.pem`), `.claude/`, `AGENTS.md`, `HANDOFF_*.md`, `CODEX_*.md`, plus standard Python/Node/IDE/macOS noise.
  - Bound Docker ports to `127.0.0.1` (was `0.0.0.0`) — closes unauthenticated mutating endpoints (`/api/settings POST`, `/api/trades/reset-paper-trades`, `/api/auto-trade/run`) to anyone on the LAN.
  - Tightened CORS `allow_origins=["*"] + allow_credentials=True` in `main.py:21-27` to `["http://localhost:5173", "http://127.0.0.1:5173"]`.
  - Created `config/settings.example.json` with redacted secrets so a fresh clone has a working template.
  - Cleaned dead AccuWeather/NOAA-CDO check from `routers/health.py` — was always returning `status: "degraded"` because AccuWeather was retired in session 7 in favor of Open-Meteo.
  - Updated frontend copy (`Glossary.jsx:63`, `Scanner.jsx:229,432`, `Settings.jsx:543`) to reference Open-Meteo instead of the retired AccuWeather.
- **Known regression NOT yet fixed** (flagged for user decision): `weather_model.py:1236,1240` uses `base_sigma=3.5` (HIGH) and `3.0` (LOW). CLAUDE.md sessions 2 & 6 documented these as 9.0 and 8.0. Either the doc is stale or the constants regressed. Narrower sigma → more extreme model probs → blockers funnel them harder → fewer trades. Tuning decision deferred.
- **Initialized git repo** for first GitHub push.
- **All 101 tests still pass**.

## What Was Done (2026-05-25, session 7)

- **Added city+segment blockers** (position_sizing.py): Discovered city+segment accuracy ranges from 14% (LOWTDEN) to 100% (LOWTSATX). 8 consistently losing combos now blocked: KXLOWTDC (28.6%), KXLOWTPHIL (28.6%), KXLOWTOKC (36.4%), KXHIGHTNOLA (37.5%), KXHIGHTSATX (37.5%), KXLOWTDAL (50.0%), KXLOWTNOLA (57.1%), KXHIGHTSFO (60.0%). Retroactive impact: accuracy 67.8% → 77.1%, P&L -$26.90 → +$30.79 across 240 remaining trades. Winning combos like KXHIGHPHIL (100%) and KXLOWTSATX (100%) are unaffected.
- **Integrated Open-Meteo** (weather_model.py): Free weather API, no key needed, no rate limits. Replaces expired AccuWeather as second forecast source. Bot now averages NWS + Open-Meteo temperatures and precipitation forecasts. Source disagreement tracking works with 2+ sources.
- **Removed 25-30c blanket block** (position_sizing.py): The 25-30c range was blocked at 65% accuracy. With city blockers applied, that range is 75.0% accurate and +$3.35 profitable. Unblocked.
- **Removed Denver-specific block** (position_sizing.py): Denver is covered by the city+segment blocker set (KXLOWTDEN, KXHIGHDEN both in blocked list). Removed the redundant regex-based Denver block.
- **Added startup trade settlement** (scheduler.py): `settle_expired_open_trades()` now runs immediately on boot, before the first scan. Prevents trades getting stuck open during downtime (24 trades were stuck from 3 days of bot being down).
- **Added isotonic calibration auto-rebuild** (scheduler.py): `rebuild_isotonic_calibration()` runs on startup and every 10 minutes in the learning refresh job. Knots are computed from 542+ clean settlements using `raw_model_prob`.
- **All 101 tests passing**.

## What Was Done (2026-05-22, session 6)

- **Fixed bracket parsing bug** (weather_model.py:1292): `-B75` was parsed as 1° bracket (75-76) instead of 2° (74-76). Model probabilities for ALL bracket markets were approximately half what they should be. This is the single biggest accuracy fix.
- **Fixed sigma bypass** (weather_model.py:1030-1051): `_adaptive_sigma()` was ignoring the `base_sigma` parameter entirely, using hardcoded 4-6 instead of the tuned 9.0 (HIGH) / 8.0 (LOW). Sigma now properly scales from base by time-to-close.
- **Fixed scanner retry never retrying** (scanner.py:286): `range(1)` meant the 429 rate-limit retry logic never actually retried. Changed to `range(3)`.
- **Removed NOAA CDO phantom source** (weather_model.py:915): Was inflating confidence (+0.06) by pretending a data source existed when it contributed no actual forecast data.
- **Fixed scheduler race condition** (scheduler.py:110): `max_instances=2` on auto_entry allowed concurrent runs that could produce duplicate trades. Set to 1.
- **Added duplicate market prevention** (auto_entry.py): Bot was entering 1.8 trades per market on average (up to 5x). One market lost $12.90 alone from 5 duplicate entries. Now checks open_trade_tickers before entering.
- **Fixed paper_unlimited_learning default** (config.py:12): Default was `True` (bypasses all blockers). If settings.json were ever lost, the bot would enter losing trades with no safety. Changed to `False`.
- **Fixed brain score circular reasoning** (weather_brain.py:262-266): Strategy filter was cherry-picking by excluding Denver and 25-30c range specifically. Simplified to just NO 20-40c excluding threshold markets.
- **Made brain metrics consistent** (weather_brain.py): Recent CLV, recent P&L, and recent prediction accuracy queries now all use the same strategy filter.
- **Removed dead calibration code**: `update_model_calibration()` and `_refresh_model_calibration()` were running on every trade close and segment rebuild but producing data that was never consumed (`_apply_calibration` was disabled). Removed all calls.
- **Removed dead code across codebase**: `_is_weather_market` (scanner), `_settle_expired_trades` (trade_lifecycle), phantom penalty constants (brain), empty TestPaperTradeCircuitBreaker class, dead settings (circuit_breaker, daily/weekly loss limits), unused HTTPException import.
- **Fixed health endpoint** (health.py): Was always returning "ok" even when services were down. Now checks DB connectivity and reports "degraded" with specific issues.
- **Deleted Performance page** (frontend): Was a weaker duplicate of Brain page with different (incorrect) scoring math.
- **Fixed CSS --blue being green** (styles.css): `--blue: #00e508` (green) → `--blue: #3b82f6` (actual blue).
- **Simplified Dashboard**: Removed autonomy-strip (redundant), KalshiChart (empty in paper mode), scan-bar footer (3rd scan status display), live clock, version constant. BrainBar reduced from 6 metrics to 3 (Accuracy, Win Rate, Settled).
- **Simplified Trades page**: Removed 4 useless metric pills (Entry Move, Stop Loss, Take Profit, Trust at Entry) and "What the bot learned" expandable section.
- **Cleaned format.js**: Removed dead exports (fmtCentsNoPlus, humanizeMarket), consolidated duplicate fmtEdge.
- **All 101 tests passing**, frontend builds cleanly.

## What Was Done (2026-05-20, session 5)

- **Tightened NO-HIGH blocker from 50c to 40c** (position_sizing.py): Data shows NO bracket trades at 40-50c entry are 43.8% accurate and -$2.70 P&L. The 20-40c range is the only profitable zone. Both paper and live modes now block NO above 40c for all market types.
- **Added threshold market blocker** (position_sizing.py): Threshold markets (-T) have 25% NO accuracy and 0% YES accuracy. Both paper and live modes now block all threshold market trades.
- **Fixed threshold detection false positives** (position_sizing.py): The `-T` substring match was triggering on test tickers like `KXHIGHNY-TEST`. Changed to regex `-T\d` which only matches actual threshold suffixes like `-T68`.
- **Confirmed profitable trading segment**: Analyzed all post-calibration data. NO bracket trades at 20-40c entry: 70.1% accuracy, +$5.28 P&L across 67 trades. The math works at 35c (pay 65c, win 35c, need 65% — getting 70%).
- **Verified automation is fully operational**: Scheduler runs scans every 15 min, auto_entry every 5 min, lifecycle checks every 5 min. No babysitting needed.
- **All 101 tests passing**.

## What Was Done (2026-05-20, session 4)

- **Root-caused why bot loses money despite high accuracy** (data analysis): NO trades at sub-20c market prices require 80-90% accuracy to break even (pay 85c to win 15c). The bot was 80% accurate there but still -$3 P&L. At 30-40c, 70% accuracy earns +$6.54 because the risk/reward works (pay 65c to win 35c).
- **Blocked NO sub-20c trades** (position_sizing.py): Both paper and live modes now block all NO trades when market YES price < 20c. Shows break-even accuracy needed in blocker message. Previously only blocked NO-LOW sub-10c.
- **Blocked NO-LOW above 40c** (position_sizing.py): LOW market NO trades above 40c are 25% accurate and losing money. Tightened from 50c → 40c cutoff.
- **Disabled isotonic calibration** (weather_model.py): The isotonic knots were trained on bulk_cleanup garbage data AND used `$.model_prob` (post-calibration) instead of `$.raw_model_prob` — circular calibration. Set knots to identity (passthrough). Fixed `rebuild_isotonic_calibration()` to use `raw_model_prob` and `market_closed` trades only for future rebuilds.
- **Strengthened market anchor** (weather_model.py): Markets priced under 5c are now capped at 8% model probability (was allowing 30%+ through). Markets priced over 95c floored at 92%. Reduced disagreement threshold from 0.40 to 0.30 for mid-range anchoring. This prevents the model from going YES on 1c markets.
- **Updated 3 tests** to use 30c market prices instead of 15c (blocked by new sub-20c rule).
- **All 101 tests passing**.

## What Was Done (2026-05-19, session 3)

- **Bulk-closed 418 garbage trades**: 355 YES-sub-10c and 63 NO-50c+ open trades closed with worst-case settlement (YES→0, NO→1). Total P&L hit: -$35.88. These were trades entered during the `paper_unlimited_learning: true` period with 0-5% accuracy.
- **Fixed brain recent-window pollution** (weather_brain.py): Bulk-closed trades all had exit_time=now, which swamped the recent-30 and recent-100 windows. Added `bulk_cleanup` exclusion to 5 recent-window queries. Overall stats still include cleanup losses (accurate), recent metrics reflect actual trading quality.
- **All 101 tests passing**.

## What Was Done (2026-05-19, session 2 — Opus review of Codex changes)

- **Fixed critical settings issue** (settings.json): `paper_unlimited_learning: true` was bypassing ALL trade blockers. 373 YES-sub-20c trades entered with 0% accuracy since May 18. Set to false, restored minimums (EV 0.02, confidence 0.35, edge 0.03), capped entries at 30/scan and 3/event.
- **Added isotonic calibration** (weather_model.py): Piecewise-linear lookup table mapping raw model probabilities to empirical settlement rates from ~700 trades. The model was massively miscalibrated — said 5% when reality was 25%, said 90% when reality was 70%. Isotonic layer corrects this. Auto-rebuilds from trade data.
- **Widened sigma** (weather_model.py): HIGH 7→9, LOW 6→8. The model was overconfident (narrow distributions). Wider sigma pushes probabilities toward 50% = less overconfidence at extremes.
- **Pruned test suite** (118→101): Removed 17 tests that checked trivial string matching, config defaults, or duplicated coverage. Trading logic tests kept.
- **Consolidated duplicated code**: `_segment_keys_from_details` was duplicated in position_sizing.py and auto_entry.py with diverging signatures. Moved to adaptive_policy.py as single source of truth.
- **Removed dead code**: `_consecutive_loss_count` (unused circuit breaker), `_extract_precip_threshold` (never called), redundant `import re`.
- **All 101 tests passing**.

## What Was Done (2026-05-19, session 1)

- **Unblocked YES trades** (position_sizing.py): Previous session blocked ALL yes trades. Data shows yes trades at 20c+ entry are 41.5% accurate and +$3.91 profitable. New rule: only block yes sub-20c (1.9% accuracy). YES and NO both trade now.
- **Fixed brain score recency weighting** (weather_brain.py): Brain was stuck at 28/100 because it averaged ALL 1,535 historical trades equally. New scoring blends 70% recent / 30% historical for CLV, positive rate, and prediction accuracy. Score jumped to 43/100 immediately.
- **Added recent metrics** (weather_brain.py): `recent_prediction_accuracy` (last 100 trades) and `recent_positive_clv_rate` (last 100 trades) now computed and used in brain score. These are what actually matter for the bot's current ability.
- **Loosened NO blockers** (position_sizing.py): Removed blanket "no 50-85c" block. Now: NO-HIGH above 50c blocked (38% accuracy), NO-LOW above 50c blocked (15%). NO trades at 10-50c range allowed for both HIGH and LOW.
- **Fixed scheduler deadlock** (scheduler.py): APScheduler was single-threaded — scan jobs (4-5 min) blocked all other background jobs. Added ThreadPoolExecutor(max_workers=4).
- **Raised open position cap** (auto_entry.py): Was 200, now 300. Bot had 209 open and was stuck.
- **Fixed 5 tests** across 2 files: All tests using YES direction alerts updated to NO direction with correct market math (model_prob must make side_prob > side_entry for positive edge).
- **All 116 tests passing**.

## What Was Done (2026-05-18, session 2)

- **Fixed critical blocker bypass** (auto_entry.py): `_paper_learning_contracts()` was ignoring position_sizing blockers — always returning ≥1 contract even when blockers said 0. Yes-sub-20c (5% accuracy) and no-85c+ (0% accuracy) trades were slipping through. Added `if recommendation.get("blockers"): return 0` guard.
- **Removed paper trading circular dependency** (auto_entry.py): `paper_auto_blocker()` required prediction accuracy >40% AND eligible segments to trade. But can't get eligible segments without trades. Gutted it — now only blocks at >200 open positions.
- **Removed weak_segment contract cap** (position_sizing.py + auto_entry.py): Everything was stuck at 1 contract because `weak_segment` logic judged all segments as bad based on overall 31% accuracy. Now scales 1-5 contracts based on EV/edge/confidence.
- **Raised entry limits** (settings.json): 50/scan (was 8), 3/event (was 1), min EV 0.02 (was 0.08), min confidence 0.35 (was 0.55).
- **Fixed P&L mismatch** (trades.py + Trades.jsx): Trades page was summing P&L from a 200-trade window. Added backend aggregate across all 1,433 trades. Summary strip now shows Total P&L, Avg Entry Move, Prediction Accuracy, Settled Trades.
- **Bot is now actively trading**: 200 open paper trades, 50-100 placed per scan, contracts scaling 1-5.
- **All 116 tests passing**.

## What Was Done Today (2026-05-18, session 1)

- **Root-cause analysis**: Ran full trade data analysis across 1,433 settled trades. Found the real problem: the bot was taking massive volumes of losing trades (yes-sub-20c = 5% accuracy, no-LOW 70-85c = 12%, no-LOW 20-30c = 8%). The no-HIGH trades at reasonable prices were 65% accurate and profitable all along.
- **Simulated blocker impact**: With new blockers applied retroactively to all historical trades, accuracy jumps from 31% → 68.9%, P&L from -$26.67 → +$11.77, CLV from -1.03c → +0.18c.
- **Expanded trade blockers** (position_sizing.py):
  - yes sub-20c blocked (was sub-10c only) — 5% accuracy over 650+ trades
  - no-LOW 70-85c blocked (12% accuracy, -$6.88)
  - no-LOW 20-30c blocked (65% accuracy but -$5.41 P&L, -5.4c CLV)
  - no-HIGH 70-85c only blocked when model severely underestimates (25% accuracy but some profitable trades)
  - Live mode now also blocks yes sub-20c, no 85c+, and data-driven patterns
- **Calibration weight increased**: Was applying only 50% of measured bias correction; now ramps linearly from 25% (5 samples) to 100% (20+ samples). Most city/segment combos have 10-25 samples with 8-16pp bias — this will meaningfully shift probabilities toward reality.
- **Live-mode safety hardened**: Added prediction accuracy check (<40% blocks entry) and verified phantom risk blocker already existed. Live mode now has same data-driven blockers as paper mode.
- **All 116 tests passing**.

## What Was Done (2026-05-15)

- Fixed critical brain bug: ALL 1,420 trades excluded from brain/learning queries due to `exit_reason='paper_reset'` filter. Removed exclusion from ~15 queries across 5 files.
- Tightened weather model sigma (HIGH 10->7, LOW 8.5->6), adaptive factors, sigma floor 4.0.
- Added paper-mode trade blockers: yes-on-cheap (<10c), yes-on-cheap-with-low-model, no-against-high-conviction.
- Adaptive risk controls: `/settings/effective-risk` endpoint + SmartRiskPanel in Settings UI.
- Alerts page overhaul with hero layout.
- Blocked "no" on expensive markets (85c+ and 70-85c).
- Market-price anchoring: blends 35% toward market at extremes (>85c or <15c).

## Architecture (Codex Handoff)

**Stack**: Python/FastAPI backend (port 8000) + React/Vite frontend (port 5173), SQLite DB at `data/sibylla.db`, Docker Compose for local dev.

### Backend (`backend/app/`)

**Routers** (API endpoints):
- `alerts.py` - GET /api/alerts, alert scoring + brain enrichment
- `auto_trade.py` - Paper auto-trade execution
- `brain.py` - GET /api/brain/status, brain trust scoring
- `scan.py` - Weather market scanner
- `settings.py` - GET/POST/PATCH /api/settings, GET /api/settings/effective-risk
- `trades.py` - Trade CRUD, history, prediction accuracy
- `overview.py` - Dashboard summary stats
- `kalshi.py` - Kalshi API proxy endpoints
- `weather_events.py` - Weather event lookup

**Services** (business logic):
- `weather_model.py` - Gaussian CDF model, adaptive sigma, isotonic calibration, city-level bias correction. THE core model.
- `weather_brain.py` - Brain trust scoring (0-100), learning sample analysis, segment tracking
- `adaptive_policy.py` - Per-segment performance tracking, auto-eligibility decisions
- `position_sizing.py` - Kelly criterion sizing, trade blockers, risk limits
- `scanner.py` - Scans Kalshi weather markets, runs model, produces alerts
- `trade_lifecycle.py` - Trade entry/exit, settlement, P&L calculation
- `auto_entry.py` - Automated paper trade entry logic
- `order_manager.py` - Order placement and management
- `kalshi_client.py` - Kalshi API client, auth, credentials check
- `scheduler.py` - Background scan scheduling

### Frontend (`frontend/src/pages/`)

Dashboard, Alerts, Scanner, Trades, Paper, Brain, Performance, Settings, Glossary

### Key Data Flow

Scanner runs -> weather_model scores each market -> alerts generated -> position_sizing evaluates -> auto_entry places paper trades -> trade_lifecycle tracks -> weather_brain learns from outcomes -> adaptive_policy updates segments -> risk controls adjust

### Database

SQLite at `data/sibylla.db`. Key tables: `trades`, `alerts`, `adaptive_segments`, `model_calibration`, `weather_events`.

### Config

`config/settings.json` - All runtime settings. Loaded via `app/config.py`. Never commit secrets.

## Known Issues

- **AccuWeather API key EXPIRED** — but no longer needed. Open-Meteo (free, no key) is now the second forecast source. AccuWeather can be re-added if you get a new API key; the code still supports it as a third source.
- **Overall P&L still negative** — -$222.11 total (includes bulk_cleanup/paper_reset garbage). Strategy-filtered P&L with city blockers is +$30.79 retroactively, but this needs live validation.
- **City blockers are retroactive estimates** — The 8 blocked combos are based on 7-15 historical trades each. Need 50+ new settlements under these rules to confirm the improvement holds forward.
- **24 open trades from May 22** — stuck because bot was down for 3 days. Will auto-settle on next Docker start.
- **Isotonic calibration knots are sparse** — 443 of 542 trades fall in the 0.1 raw_model_prob bucket. Calibration helps but the model's probability distribution is narrow.

## Trade Accuracy Data (updated 2026-05-25, session 7, ALL market_closed trades)

By entry price bucket for NO bracket trades (WITH city+segment blockers applied):
```
Entry   | Trades | Wins | Accuracy |   P&L    | Status
--------|--------|------|----------|----------|--------
sub-20c |    ~40 |  ~33 |   ~83%   | -$6.xx   | BLOCKED (need 85%+ to profit)
20-30c  |    156 |  121 |   77.6%  | +$18.14  | ACTIVE
30-40c  |     84 |   64 |   76.2%  | +$12.65  | ACTIVE
40-50c  |    ~30 |  ~13 |   ~43%   | -$10.xx  | BLOCKED
yes all |     26 |    0 |    0.0%  |  -$0.86  | BLOCKED
```

City+segment performance (top & bottom, 5+ samples, NO 20-40c bracket):
```
City+Segment  | Trades | Accuracy |   P&L    | Status
--------------|--------|----------|----------|--------
KXLOWTSATX    |     10 |  100.0%  |  +$5.11  | ACTIVE (best)
KXLOWTMIN     |      8 |  100.0%  |  +$4.44  | ACTIVE
KXHIGHPHIL    |      9 |  100.0%  |  +$7.81  | ACTIVE
KXHIGHLAX     |     10 |   90.0%  |  +$3.84  | ACTIVE
KXLOWTDEN     |      7 |   14.3%  |  -$8.60  | BLOCKED
KXLOWTPHIL    |      7 |   28.6%  |  -$8.12  | BLOCKED
KXLOWTDC      |      7 |   28.6%  |  -$7.41  | BLOCKED
KXHIGHTSFO    |     15 |   60.0%  |  -$4.08  | BLOCKED
```

Current blocker rules (position_sizing.py):
- YES: all blocked (0% accuracy)
- NO sub-20c: blocked (can't profit even at 83% accuracy)
- NO 40c+: blocked (43% accuracy for HIGH, 25% for LOW)
- Threshold markets (-T): blocked (25% NO accuracy)
- 8 city+segment combos: blocked (14-60% accuracy, -$45.69 total P&L)
- Bracket within 2° of forecast: blocked
- Duplicate market tickers: blocked

## Rules

- NEVER delete old/archived trades. The bot learns from historical data. Even bad trades are valuable training signal.
- NEVER bypass live-trading safety gates. Brain score must be 90+ with all conditions met.
- Keep paper_reset trades in all queries. They are real trades with real outcomes.
- Be mindful of token usage. Don't read entire large files unnecessarily.
- Voice-dictated messages may contain typos. "Kalshi" might appear as "Kelsey", "alerts" as "a alerts", etc.
