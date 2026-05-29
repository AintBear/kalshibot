# KalshiBot Watchdog

The watchdog is a small macOS/launchd safety loop for the local Docker runtime.
It does not enable live trading.

Every five minutes it:

- starts Docker Compose services if they are down
- restarts the backend when `/health` fails
- restarts the backend when a scan appears stuck
- triggers a fresh scan when the last scan is stale
- pokes paper auto-entry after a completed scan when paper auto is enabled and the backend says it is ready

## Install on the Mac

Do not install the LaunchAgent from `~/Downloads`, `~/Desktop`, or `~/Documents`.
macOS privacy controls can block background jobs from reading those folders. Use
a normal project folder such as `~/Projects/kalshibot`.

From the repo root:

```bash
chmod +x scripts/watchdog.sh scripts/install-watchdog-launchd.sh
scripts/install-watchdog-launchd.sh
```

Logs are written to:

```text
logs/watchdog.log
logs/watchdog.launchd.out.log
logs/watchdog.launchd.err.log
```

## Dry Run

```bash
scripts/watchdog.sh --dry-run
```

## Verify

```bash
launchctl print gui/$(id -u)/com.sibylla.kalshibot.watchdog | sed -n '1,120p'
tail -n 80 logs/watchdog.log
tail -n 80 logs/watchdog.launchd.err.log
```

If `launchctl` shows exit code `126` and stderr includes `Operation not
permitted`, the repo is probably in a protected macOS folder. Move it outside
`Downloads`, `Desktop`, or `Documents`, then reinstall.

## Keep the Mac Awake

The LaunchAgent runs only while the Mac is awake and your user session is active.
For a 24/7 local bot, keep the Mac plugged in and prevent system sleep:

```bash
sudo pmset -a sleep 0
sudo pmset -a disksleep 0
```

Display sleep is fine. System sleep is not.

## Uninstall

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.sibylla.kalshibot.watchdog.plist
```

## Defaults

Override these with environment variables if needed:

```text
KALSHIBOT_BACKEND_URL=http://127.0.0.1:8000
KALSHIBOT_MAX_SCAN_AGE_MIN=45
KALSHIBOT_MAX_RUNNING_SCAN_MIN=20
KALSHIBOT_WATCHDOG_INTERVAL_SECONDS=300
```
