Write-Host "Sibylla Weather Bot diagnostics"
Get-Date
Write-Host ""

Write-Host "-- containers --"
docker compose ps
Write-Host ""

Write-Host "-- backend health --"
try { (Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing).Content } catch { Write-Host "(unreachable)" }
Write-Host ""

Write-Host "-- overview --"
try { (Invoke-WebRequest -Uri "http://localhost:8000/api/overview" -UseBasicParsing).Content } catch { Write-Host "(unreachable)" }
Write-Host ""

Write-Host "-- alerts --"
try { (Invoke-WebRequest -Uri "http://localhost:8000/api/alerts" -UseBasicParsing).Content } catch { Write-Host "(unreachable)" }
Write-Host ""

Write-Host "-- backend logs --"
docker compose logs --tail=120 backend
