# Sibylla Deployment

The bot is one Python backend + one SQLite database. **Run exactly one
backend instance** because SQLite is single-writer. There are two ways
to run it:

1. **Local Docker** (development, what you're running now)
2. **Fly.io** (production, recommended) — survives your laptop sleeping,
   your power going out, and your Mac being in the car

---

## Local Docker (dev)

```bash
cp config/settings.example.json config/settings.json
# edit config/settings.json with your Kalshi key id
# drop your RSA private key at config/kalshi_private_key.pem

docker compose up -d
curl http://localhost:8000/health
open http://localhost:5173            # frontend
```

Restart policy: `unless-stopped`. Docker Desktop must launch on Mac boot
or the bot doesn't come back. macOS sleep kills it.

For longer-running local hosting, install the watchdog
([`scripts/install-watchdog-launchd.sh`](scripts/install-watchdog-launchd.sh))
and disable sleep with `sudo pmset -a sleep 0`. See [`docs/WATCHDOG.md`](docs/WATCHDOG.md).

---

## Fly.io (production)

Cost: ~$0–$7/month. Fly's free allowance currently covers one
shared-cpu-1x machine with two small volumes. Past free, you pay
~$2/month for compute + ~$1.50/month for storage.

### Why Fly

- Deploy from `git push` with one command
- Persistent volumes for the SQLite file + the secrets directory
- HTTPS termination + a healthcheck that restarts on `/health` 503
- No DNS/Nginx/Cloudflare to configure
- The runtime your Mac and PC can't be: always on

### One-time setup

```bash
# 1. Install + log in
brew install flyctl
fly auth login

# 2. Create the app (uses the existing fly.toml — pick a unique name)
fly launch --copy-config --no-deploy
#    when prompted: app name = sibylla-kalshibot (or another unused name),
#    region = iad (or whichever is closest to you).

# 3. Create the two persistent volumes
fly volumes create sibylla_data   --region iad --size 1
fly volumes create sibylla_config --region iad --size 1

# 4. First deploy — this builds the image and starts the machine.
#    Health will fail until step 5 because /app/config/settings.json is empty.
fly deploy

# 5. Upload your live settings + RSA key into the persistent config volume.
#    These never leave the machine and are NOT stored as fly secrets.
scripts/fly-bootstrap-secrets.sh

# 6. Restart so the new settings take effect
fly machine restart --app sibylla-kalshibot

# 7. Verify
curl https://sibylla-kalshibot.fly.dev/health
curl https://sibylla-kalshibot.fly.dev/api/brain/status | jq '.score, .score_breakdown.biggest_gap'
```

### Day-2 deploys

```bash
git push                    # pushes the change
scripts/fly-deploy.sh       # rebuilds + deploys + runs a health sweep
```

### Logs / SSH / inspect

```bash
fly logs --app sibylla-kalshibot
fly ssh console --app sibylla-kalshibot -C 'sqlite3 /app/data/sibylla.db "PRAGMA quick_check;"'
fly ssh console --app sibylla-kalshibot -C 'ls -la /app/config /app/data'
```

### Rotating the Kalshi key

```bash
# overwrite the PEM and bounce the machine
fly ssh sftp shell --app sibylla-kalshibot <<EOF
put config/kalshi_private_key.pem /app/config/kalshi_private_key.pem
EOF
fly machine restart --app sibylla-kalshibot
```

### Tearing it down

```bash
fly apps destroy sibylla-kalshibot --yes
fly volumes destroy sibylla_data --yes
fly volumes destroy sibylla_config --yes
```

---

## Constraints that apply to every host

- **Exactly one backend replica.** SQLite is single-writer. If you scale up,
  you corrupt the database.
- **Mount `/app/data` and `/app/config` as persistent volumes.** Both contain
  state that must survive a redeploy: the database, the calibration table,
  the settings file, the RSA key.
- **Keep SQLite in rollback journal mode.** `PRAGMA quick_check` should
  return `ok`. Do NOT enable WAL unless the storage backend supports
  SQLite sidecar files atomically — Fly's volumes do, but some bind-mount
  setups don't.
- **Never paste the RSA private key into a chat or commit it.** Put the
  PEM file at `config/kalshi_private_key.pem` locally and let
  `fly-bootstrap-secrets.sh` upload it via SFTP. The repo's `.gitignore`
  excludes `*.pem` and `config/kalshi_private_key.pem` explicitly.
- **Live trading stays gated by `paper_trading: false` in settings.json,
  brain score ≥ 90, and `entry_quality_ok = true`.** The deployment doesn't
  change any of this — flipping live mode is a separate, deliberate edit
  to the settings file on the volume.

---

## What this deployment does NOT include yet

- **Frontend.** The `frontend/` React app is not yet deployed to Fly.
  Easiest path is to add it as a second Fly app pointing at the backend
  URL, or build a static bundle and serve it from the backend (single app,
  one less moving piece). For now you can run the frontend locally and
  point `VITE_API_URL` at `https://sibylla-kalshibot.fly.dev`.
- **CI auto-deploy.** Adding a GitHub Action that runs `fly deploy` on
  `main` push is a 10-line `.github/workflows/deploy.yml`; left out so
  you can decide whether to wire that up or keep deploys manual.
