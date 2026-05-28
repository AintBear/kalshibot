# Sibylla Deployment Notes

## Local Auto-Restart

`docker-compose.yml` already uses `restart: unless-stopped` for backend and frontend. To avoid manual restarts after reboot, Docker Desktop must start when macOS logs in. Once Docker is running, these containers should come back automatically.

Run:

```bash
docker compose up -d
```

Then verify:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/scan/status
```

## Cloud Shape

Use one backend replica while SQLite is the database.

Required mounts:

- `/app/data` for persistent `sibylla.db`
- `/app/config` for `settings.json` and private key files

Keep SQLite in rollback journal mode. Do not enable WAL unless the storage backend supports SQLite sidecar files reliably.

Suggested first deployment:

- Backend: container service exposing port `8000`
- Frontend: container/static service exposing port `5173` or a built static site pointed at the backend URL
- Persistent volume mounted to `/app/data`
- Secret/config volume mounted to `/app/config`

Do not paste RSA private keys into chat. Put the PEM file under the mounted config directory and set `kalshi_private_key_path` to that file path in Settings.
