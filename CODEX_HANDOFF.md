# Codex Handoff — what to pick up next session

This is the running handoff between Codex and Claude. Both agents
coordinate through GitHub PRs (PR #1 = Codex session 11, PR #2 = Claude
sessions 14–16, both open against `main`). Review prompts live in PR
descriptions and as PR comments. **Read PR #2's body and the three
session comments first** — they list the verification asks for each
shipped change.

## Live state as of session 16

- Runtime: **`/Users/AintBear/Projects/kalshibot`** with launchd watchdog
  (`com.sibylla.kalshibot.watchdog`)
- Branch: `claude/wonderful-heisenberg-512338` at commit `10e9093`,
  stacked on `codex/session11-runtime-coordination` (PR #2 → PR #1)
- Brain score: 82. Biggest gap: `clv +6.48 (blended CLV +0.68c, max at +5c)`
- Live trading: off and gated. Don't flip.
- 145/145 backend tests pass on the Codex review branch

## Codex review update as of 2026-06-01

Codex reviewed PR #2 and found three operational blockers in the handoff
items:

- `scripts/watchdog.sh` treated any `/health` 503 as an immediate backend
  restart before reaching the scan-specific decision path. That could restart
  on stale scans and repeat restarts for the same high-error scan. Fixed with
  health classification, scan-degraded deferral, and a restart marker/cooldown.
- The Fly path pointed `fly.toml` at `backend/Dockerfile`, but Fly keeps the
  Docker build context at the repo root when running `fly deploy` from the
  repo root. Added `Dockerfile.fly`, pointed `fly.toml` at it, and added
  `.dockerignore` so local DB/config/key files are not sent to Docker/Fly build
  context.
- The scanner's stored recommendation passed only a narrow nested details dict
  into `position_sizing`, so the new `min_volume_24h`/`min_open_interest`
  blockers could be missing from serialized scan recommendations even though
  auto-entry/live-readiness re-ran sizing correctly. Fixed by passing the full
  alert details dict.

Verification from this pass: backend tests 145 passed, frontend build passed,
local runtime `/health` OK, latest scan 535/535 with 0 series errors, DB
`quick_check` OK, raw forecast and intraday fields persisted, live remains
blocked (`paper_trading=true`, `entry_quality_ok=false`), Fly image builds and
smoke `/health` returns OK on an empty config volume with automation off.

## Items for Codex (in priority order)

### 1. Verify the session 14–16 changes (PR #2 review prompts)

Each session left explicit review questions in PR comments:

- **Session 14** (commit `5b561c9`): `/health` scan-state hardening + watchdog
  `scan_high_error_rate` path. Review prompts in PR #2 body.
- **Session 15** (commit `0f27e5b`): paper midpoint fill model + intraday
  observation override. Review prompts in PR #2 comment "Session 15 update".
- **Session 16** (commit `10e9093`): liquidity floor + ECMWF source +
  brain score breakdown + slice-aware calibration with session-4
  safeguards. Review prompts in PR #2 comment "Session 16 update".

If anything passes review, close it out by approving the PR. If anything
fails, push a fix to `codex/session11-runtime-coordination` and post a
comment on PR #2 with what changed and why.

### 2. Help the user move to Fly.io

`DEPLOYMENT.md` now has the full Fly.io path. `fly.toml`,
`scripts/fly-bootstrap-secrets.sh`, and `scripts/fly-deploy.sh` are in
the repo. What's NOT done:

- Frontend deployment (currently runs locally only)
- GitHub Actions auto-deploy on `main` push
- Initial provisioning (requires the user's Fly credentials)

If the user invokes you with Fly credentials available, run through the
one-time setup in `DEPLOYMENT.md`. The bootstrap script handles secret
upload safely (SFTP into the persistent volume, never stored as Fly
secrets so they can be rotated without a redeploy).

### 3. Live limit-order management in `order_manager.py`

When the live gate eventually opens (recent CLV positive, brain ≥ 90,
`entry_quality_ok = true`), live mode still defaults to `live_fill_model
= "ask"` because the bot doesn't yet post real limit orders. The paper
side simulates midpoint fills; live needs the order-management layer.

Concretely:

- `backend/app/services/order_manager.py` should grow a "post passive
  limit at bid+1c, cancel and re-post on quote move, cross to ask if the
  alert is < N minutes from expiry" flow.
- Track fill rate per (city, segment) so we can validate the paper
  midpoint assumption against reality.
- `position_sizing` already exposes `fill_model`, `side_bid`,
  `side_ask` on every recommendation — order_manager just needs to use
  them.

Do NOT build this until the user explicitly asks. Paper-only is the
correct posture until forward-validation shows positive recent
expectancy.

### 4. Forward-validation checkpoint

After ~30 fresh settlements under the session 15/16 rules (limit fills,
intraday obs, ECMWF, liquidity floor, slice calibration with safeguards),
re-run the analysis:

- Has recent-30 CLV crossed zero? (`/api/brain/status.recent_30_avg_clv`)
- Has `entry_quality_ok` flipped to true?
- Has the `BIGGEST GAP` component in the brain breakdown moved off CLV?
- Have any (city, market_type) slices in `model_calibration` crossed 20
  samples and started applying?

If yes: the engineering changes worked. Brief Claude/the user on the
gate state and let them decide on live mode.

If no: the bottleneck is the model itself, not the plumbing. The next
move is per-slice model retuning — likely a slice-specific sigma table
in `weather_model._adaptive_sigma` based on which (city, segment) slices
the calibration table shows as having the highest variance between
`avg_model_prob` and `avg_settlement_rate`.

### 5. Slice calibration sanity check after 20+ samples

Currently no (city, market_type) slice has crossed the 20-sample apply
threshold — the calibration loads but doesn't fire. Once the first
slices cross 20, sanity-check before trusting them:

```bash
sqlite3 data/sibylla.db "
  SELECT city, market_type, sample_count, calibration_bias,
         avg_model_prob, avg_settlement_rate
    FROM model_calibration
   WHERE sample_count >= 20
   ORDER BY ABS(calibration_bias) DESC
"
```

Watch for:
- Any city showing `|calibration_bias| > 0.20` with low sample count
  (20-30) — would be clamped to ±0.15 with 50% weight, but worth flagging
- Repeated YES-side bias on cities the bot has historically blocked
- Drift over time: re-run the same query weekly and check whether biases
  are converging or oscillating

### 6. AccuWeather doc/code reconciliation

CLAUDE.md sessions 7 + 13 are inconsistent about AccuWeather's status.
Runtime: `accuweather_cache.status=live`, the key in `config/settings.json`
is active, and `_fetch_accuweather_forecast` does get called. Either:
- Update CLAUDE.md to say "AccuWeather is active and contributing to
  source averaging at weight 0.40", or
- If AccuWeather should actually be removed (the user has a strong
  opinion), drop the fetch code, the cache, and the weight.

Not blocking anything but flagged so it doesn't drift further.

## What Claude should do next session

- Read this file
- Read the latest CLAUDE.md session entry (currently session 16)
- Check `/api/brain/status.score_breakdown.biggest_gap` to see whether
  the engineering changes have moved the needle
- Don't ship more features until forward-validation tells us if the
  plumbing fixes worked or not

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
