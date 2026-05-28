$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "Sibylla Weather Bot - clean rebuild"
Write-Host ""

docker compose down
docker compose build --no-cache
docker compose up -d

Write-Host "Waiting for backend..."
Start-Sleep -Seconds 10

try {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing
    Write-Host $response.Content
} catch {
    Write-Host "Health check failed - check: docker compose logs backend"
}

Write-Host ""
Write-Host "Dashboard: http://localhost:5173"
Write-Host "API docs:  http://localhost:8000/docs"
Write-Host ""
