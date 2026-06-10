# Codex Handoff — what to pick up next session

This is the running handoff between Codex and Claude. Both agents
coordinate through GitHub PRs. Review prompts live in PR descriptions and
as PR comments. **Read this file + the latest CLAUDE.md session entry
first.**

## Live state as of session 19 (2026-06-04)

- Runtime: **`/Users/AintBear/Projects/kalshibot`** with launchd watchdog
  (`com.sibylla.kalshibot.watchdog`). Backend healthy at
  `http://localhost:8000`.
- Brain score: **66** (was 82 at session 17 — see below for why this is
  not a real regression).
- Recent_30 avg CLV: **-10.13c**. Recent_30 P&L: **-$8.55**.
  `entry_quality_ok=false`. Live trading correctly gated off — don't flip.
- Open paper trades: 21. Overall realized P&L: -$80.33 across 706
  settlements. Strategy zone (NO 20-40c bracket, blocked cities excluded,
  non-threshold): +$20.80 on 364 settlements at 73.9% win rate.
- 146/146 backend tests pass on `main`.

## What landed in sessions 18 + 19

- **PR #5** (session 18, merged in session 19 as `c712ce5`): Fly.io deploy
  hardening. `fly.toml` grace period 30s → 60s. CORS allowlist +
  `CORS_ORIGINS` env var. New `scripts/fly-smoke.sh`. `scripts/fly-deploy.sh`
  now gates on smoke before declaring success.
- **`.github/workflows/deploy.yml`** (session 19): GitHub Actions
  auto-deploy. Triggers on `workflow_run` completion of `tests` on `main`.
  No-ops if `FLY_API_TOKEN` is not set. `workflow_dispatch` available
  for manual.
- **CLAUDE.md AccuWeather reconciliation** (session 19): doc no longer
  claims AccuWeather is "expired". Runtime: live, weight 0.40, one of
  four active forecast sources.
- **No trading-logic changes.** No blocker, sigma, calibration safeguard,
  or scheduler changes.

## The CLV "regression" — what it actually is

Recent_30 dropped from session 17's +0.68c to -10.13c. **It is not a
failure of the session 15-16 engineering work.** Investigation in
session 19 narrowed it to a single bad weather day:

- 2026-06-03 alone contributed -$10.47 / -0.119 CLV.
- 31 NO-bracket trades from a single scan, 25/30 sized at 3 contracts,
  11 lost.
- The brain's `recent_30` is `LIMIT 30 ORDER BY exit_time DESC`. That one
  day plus one straggler from 2026-06-02 *is* the window.
- The bot was still 63% accurate on the window — above break-even. The
  losses were just clustered and large.

Daily CLV across the last 21 days has a median near zero with 2026-06-03
as the only meaningful outlier.

## Items in priority order

### 1. Wait for 2-3 more scan/settlement cycles before changing anything

The right move right now is patience. The recent_30 window will roll
off 2026-06-03 as new settlements arrive. If the median day continues to
sit near zero, `entry_quality_ok` will flip true without any further
engineering. **Do not ship trading-logic changes until the window has
rolled past 2026-06-03.**

### 2. Per-weather-event sizing cap (highest-leverage next engineering change)

When one scan stamps 31 same-direction NO-bracket trades on the same
weather day and most are sized at max contracts, the bot's P&L on that
day is essentially "is the weather model right about *this one day*."
That's avoidable concentration risk.

Concretely: in `backend/app/services/position_sizing.py` or `auto_entry.py`,
add a per-(weather-event-date, city) sizing cap. E.g. max 1 contract per
event-day per city after the first 3 trades. Tune by simulating against
the historical book.

Do not ship until item #1 plays out — first see if patience alone
restores the window.

### 3. Fix `fill_model` not landing in `alerts.details`

Session 15 added `fill_model` to the recommendation result but it isn't
being persisted into `alerts.details` rows. Without this, we **cannot
forward-validate** the midpoint vs ask fill question from history. The
hypothesis "midpoint is too optimistic and we're booking phantom edge"
remains untested.

Trace the path: `position_sizing.recommend_alert` → wherever the result
is serialized into `alerts.details` JSON. Add `fill_model`, `side_bid`,
`side_ask` to the persisted structure. Backfill is impossible —
forward-only.

### 4. Investigate why all 38 calibration slices have positive bias

`SELECT * FROM model_calibration ORDER BY ABS(calibration_bias) DESC`
shows every slice with bias between +0.08 and +0.49 — all in the same
direction. That means raw model_prob is *consistently* below empirical
settlement rate across every (city, market_type).

If real, this is a sigma question (Gaussian is too wide → too much mass
in the tails → underestimates probability of staying inside the bracket
→ slice bias raises model_prob to fix it). Could be cleanly addressed by
narrowing the base sigma or by making `_adaptive_sigma` slice-aware.

If artifact: check whether `raw_model_prob` is being computed pre- or
post-anchor. If post-anchor, the anchor's pull toward market price is
already biasing it, and the slice "bias" is reflecting that, not the
raw model.

### 5. Live limit-order management in `order_manager.py`

When the live gate eventually opens, live mode still defaults to
`live_fill_model = "ask"` because the bot doesn't post real limit
orders. Paper simulates midpoint; live needs the order-management layer.

Sketch:
- Post passive limit at bid+1c.
- Cancel + re-post if quotes move.
- Cross to ask if the alert is < N minutes from expiry.
- Track fill rate per (city, segment) to validate the paper midpoint
  assumption.
- `position_sizing` already exposes `fill_model`, `side_bid`, `side_ask`
  on every recommendation — `order_manager` just needs to use them.

Do NOT build this until the user explicitly asks. Paper-only is correct
until forward-validation shows positive recent expectancy.

### 6. Smoke script gap — assert live_auto is off

`scripts/fly-smoke.sh` doesn't assert `live_auto_enabled == False`. The
smoke would silently pass if someone accidentally flipped live mode on
the Fly volume. 5-line addition; not urgent because the gate has other
checks, but worth doing.

## How Claude and Codex coordinate

GitHub-mediated. Each session:

1. Open a PR (or comment on the existing one) with **explicit review
   prompts** in the body — list 3–5 numbered things the other agent
   should verify before approving.
2. When picking up the other agent's work, read the PR body, comments,
   and run through the review prompts. Post findings as a PR comment.
3. Don't merge each other's PRs without going through the prompts.
4. If you find a real bug in the other agent's work, push a fix to a
   new branch and open a follow-up PR — don't silently amend.

Repo: <https://github.com/AintBear/kalshibot>
Open PRs: <https://github.com/AintBear/kalshibot/pulls>
