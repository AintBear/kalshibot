#!/usr/bin/env bash
set -euo pipefail

echo ""
echo "Sibylla Weather Bot"
echo "Starting Docker services..."
echo ""

mkdir -p data config

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker Desktop first."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop first."
  exit 1
fi

docker compose up --build -d

echo ""
echo "Dashboard: http://localhost:5173"
echo "Backend:   http://localhost:8000/health"
echo "Logs:      docker compose logs -f backend"
echo ""
