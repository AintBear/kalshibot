#!/usr/bin/env bash
set -euo pipefail

echo "Sibylla Weather Bot diagnostics"
date
echo ""

echo "-- containers --"
docker compose ps || true
echo ""

echo "-- backend health --"
curl -sS http://localhost:8000/health || true
echo ""
echo ""

echo "-- overview --"
curl -sS http://localhost:8000/api/overview || true
echo ""
echo ""

echo "-- alerts --"
curl -sS http://localhost:8000/api/alerts || true
echo ""
echo ""

echo "-- backend logs --"
docker compose logs --tail=120 backend || true
