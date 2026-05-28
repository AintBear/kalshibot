#!/usr/bin/env bash
set -euo pipefail

echo ""
echo "Sibylla Weather Bot - clean rebuild"
echo ""

docker compose down
docker compose build --no-cache
docker compose up -d

echo "Waiting for backend..."
sleep 10

curl -f http://localhost:8000/health
echo ""
echo "Dashboard: http://localhost:5173"
echo "API docs:  http://localhost:8000/docs"
echo ""
