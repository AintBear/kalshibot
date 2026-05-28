$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "Sibylla Weather Bot"
Write-Host "Starting Docker services..."
Write-Host ""

if (-not (Test-Path "data"))   { New-Item -ItemType Directory -Path "data"   | Out-Null }
if (-not (Test-Path "config")) { New-Item -ItemType Directory -Path "config" | Out-Null }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop"
    exit 1
}

$dockerInfo = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is not running. Start Docker Desktop first, then re-run this script."
    exit 1
}

docker compose up --build -d

Write-Host ""
Write-Host "Dashboard: http://localhost:5173"
Write-Host "Backend:   http://localhost:8000/health"
Write-Host "Logs:      docker compose logs -f backend"
Write-Host ""
