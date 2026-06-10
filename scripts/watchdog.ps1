# Kalshibot Windows watchdog — PowerShell port of scripts/watchdog.sh.
# Run every 5 minutes by Task Scheduler (see install-watchdog-windows.ps1).
#
# What it does, in order:
#   1. Ensure the Docker daemon is up (starts Docker Desktop if not).
#   2. docker compose up -d (no-op when already running).
#   3. /health check: backend dead/degraded -> restart backend.
#      Scan-only degradation defers to the scan decision below.
#   4. Scan decision: stuck or high-error-rate scans -> backend restart
#      (with a marker + cooldown so the same bad scan never causes restart
#      loops); missing/stale/failed -> trigger a fresh scan; running -> wait.
#   5. Poke paper auto-entry only when the backend says it is enabled, ready,
#      and live auto is OFF.
#
# It NEVER enables live trading. It never touches settings.
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"
$RepoDir   = if ($env:KALSHIBOT_REPO_DIR) { $env:KALSHIBOT_REPO_DIR } else { Split-Path -Parent $PSScriptRoot }
$BackendUrl = if ($env:KALSHIBOT_BACKEND_URL) { $env:KALSHIBOT_BACKEND_URL } else { "http://127.0.0.1:8000" }
$MaxScanAgeMin     = if ($env:KALSHIBOT_MAX_SCAN_AGE_MIN) { [int]$env:KALSHIBOT_MAX_SCAN_AGE_MIN } else { 45 }
$MaxRunningScanMin = if ($env:KALSHIBOT_MAX_RUNNING_SCAN_MIN) { [int]$env:KALSHIBOT_MAX_RUNNING_SCAN_MIN } else { 20 }
$ErrorRateThreshold = if ($env:KALSHIBOT_SCAN_ERROR_RATE_THRESHOLD) { [double]$env:KALSHIBOT_SCAN_ERROR_RATE_THRESHOLD } else { 0.25 }
$RestartCooldownMin = if ($env:KALSHIBOT_SCAN_RESTART_COOLDOWN_MIN) { [int]$env:KALSHIBOT_SCAN_RESTART_COOLDOWN_MIN } else { 20 }
$LogDir = Join-Path $RepoDir "logs"
$LogFile = Join-Path $LogDir "watchdog.log"
$MarkerFile = Join-Path $LogDir "watchdog.scan_restart"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force $LogDir | Out-Null }

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"), $Message
    $line | Out-File -FilePath $LogFile -Append -Encoding utf8
    Write-Output $line
}

function Invoke-Watched([string]$Description, [scriptblock]$Action) {
    if ($DryRun) { Write-Log "DRY_RUN $Description"; return $true }
    Write-Log "RUN $Description"
    try { & $Action | Out-File -FilePath $LogFile -Append -Encoding utf8; return $true }
    catch { Write-Log "ERROR $Description failed: $($_.Exception.Message)"; return $false }
}

function Test-DockerReady {
    $null = docker info 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Confirm-Docker {
    if (Test-DockerReady) { return $true }
    Write-Log "Docker daemon unavailable"
    $exe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $exe) {
        if ($DryRun) { Write-Log "DRY_RUN start Docker Desktop" } else {
            Write-Log "Starting Docker Desktop"
            Start-Process $exe
            $deadline = (Get-Date).AddMinutes(4)
            while ((Get-Date) -lt $deadline) {
                Start-Sleep -Seconds 10
                if (Test-DockerReady) { return $true }
            }
        }
    }
    if (Test-DockerReady) { return $true }
    Write-Log "ERROR Docker still unavailable"
    return $false
}

function Get-Json([string]$Url, [int]$TimeoutSec = 10) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec }
    catch { return $null }
}

function Restart-Backend {
    Invoke-Watched "docker compose restart backend" { docker compose restart backend } | Out-Null
    if (-not $DryRun) { Start-Sleep -Seconds 12 }
}

function Get-HealthDecision {
    try {
        $resp = Invoke-WebRequest -Uri "$BackendUrl/health" -UseBasicParsing -TimeoutSec 10
        $body = $resp.Content
    } catch {
        # 503 responses land here too; read the body if present.
        if ($_.Exception.Response) {
            try {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $body = $reader.ReadToEnd()
            } catch { return "backend_unreachable" }
        } else { return "backend_unreachable" }
    }
    try { $data = $body | ConvertFrom-Json } catch { return "health_unreadable" }
    $issues = @()
    if ($data.issues) { $issues = @($data.issues | ForEach-Object { [string]$_ }) }
    if ($data.status -eq "ok" -and $issues.Count -eq 0) { return "health_ok" }
    $scanOnly = $true
    foreach ($issue in $issues) { if (-not $issue.StartsWith("scan_")) { $scanOnly = $false } }
    if ($issues.Count -gt 0 -and $scanOnly) { return "scan_degraded" }
    return "backend_degraded"
}

function ConvertTo-UtcTime($Value) {
    if (-not $Value) { return $null }
    $raw = [string]$Value -replace " ", "T"
    try { return ([DateTimeOffset]::Parse($raw, $null, [System.Globalization.DateTimeStyles]::AssumeUniversal)).UtcDateTime }
    catch { return $null }
}

function Get-ScanDecision($Scan) {
    if ($null -eq $Scan) { return "scan_unreadable" }
    $now = (Get-Date).ToUniversalTime()
    if ($Scan.status -eq "running") {
        $started = ConvertTo-UtcTime $Scan.started_at
        if ($started -and ($now - $started).TotalMinutes -gt $MaxRunningScanMin) { return "scan_stuck" }
        return "scan_running"
    }
    $completed = ConvertTo-UtcTime $Scan.completed_at
    $seriesTotal = [int]($Scan.series_total | ForEach-Object { if ($_) { $_ } else { 0 } })
    $seriesErrors = [int]($Scan.series_errors | ForEach-Object { if ($_) { $_ } else { 0 } })
    $marketsProcessed = [int]($Scan.markets_processed | ForEach-Object { if ($_) { $_ } else { 0 } })
    $errorRate = 0.0
    if ($seriesTotal -gt 0) { $errorRate = $seriesErrors / $seriesTotal }

    if ($null -eq $completed) { return "scan_missing" }
    if (($now - $completed).TotalMinutes -gt $MaxScanAgeMin) { return "scan_stale" }
    if ($seriesErrors -gt 0 -and $marketsProcessed -eq 0) { return "scan_failed" }
    if ($seriesTotal -gt 0 -and $errorRate -ge $ErrorRateThreshold) { return "scan_high_error_rate" }
    return "scan_ok"
}

function Get-ScanMarker($Scan) {
    if ($null -eq $Scan) { return "" }
    foreach ($key in @("completed_at", "started_at")) {
        if ($Scan.$key) { return [string]$Scan.$key }
    }
    return ""
}

function Test-RestartAllowed([string]$Decision, [string]$Marker) {
    if (-not $Marker -or -not (Test-Path $MarkerFile)) { return $true }
    try { $parts = (Get-Content $MarkerFile -TotalCount 1) -split "`t" } catch { return $true }
    if ($parts.Count -lt 3) { return $true }
    $lastDecision, $lastMarker, $lastEpoch = $parts[0], $parts[1], $parts[2]
    if ($lastDecision -eq $Decision -and $lastMarker -eq $Marker) { return $false }
    $nowEpoch = [int][double]::Parse((Get-Date -UFormat %s))
    if ($lastEpoch -match '^\d+$' -and ($nowEpoch - [int]$lastEpoch) -lt ($RestartCooldownMin * 60)) { return $false }
    return $true
}

function Save-RestartMarker([string]$Decision, [string]$Marker) {
    $epoch = [int][double]::Parse((Get-Date -UFormat %s))
    "$Decision`t$Marker`t$epoch" | Out-File -FilePath $MarkerFile -Encoding utf8
}

function Invoke-ScanTrigger {
    if ($DryRun) { Write-Log "DRY_RUN POST $BackendUrl/api/scan/weather"; return $true }
    Write-Log "POST $BackendUrl/api/scan/weather"
    try { $null = Invoke-RestMethod -Method Post -Uri "$BackendUrl/api/scan/weather" -TimeoutSec 10; return $true }
    catch { Write-Log "Scan trigger failed: $($_.Exception.Message)"; return $false }
}

function Invoke-AutoEntryIfSafe {
    $status = Get-Json "$BackendUrl/api/auto-trade/status"
    if ($null -eq $status) { Write-Log "Auto-entry status unavailable"; return }
    if ($status.live_auto_enabled -eq $true) {
        Write-Log "Live auto is enabled; watchdog will not poke auto-entry"
        return
    }
    if ($status.paper_auto_enabled -ne $true) { Write-Log "Paper auto disabled; nothing to poke"; return }
    if ($status.paper_ready -ne $true) { Write-Log "Paper auto not ready; respecting backend blocker"; return }
    if ($DryRun) { Write-Log "DRY_RUN POST $BackendUrl/api/auto-trade/run"; return }
    Write-Log "POST $BackendUrl/api/auto-trade/run"
    try { $null = Invoke-RestMethod -Method Post -Uri "$BackendUrl/api/auto-trade/run" -ContentType "application/json" -Body "{}" -TimeoutSec 60 }
    catch { Write-Log "Auto-entry poke failed: $($_.Exception.Message)" }
}

# ---------------------------- main ----------------------------

Write-Log "Watchdog tick repo=$RepoDir dry_run=$($DryRun.IsPresent)"
Set-Location $RepoDir

if (-not (Confirm-Docker)) { exit 1 }
Invoke-Watched "docker compose up -d" { docker compose up -d } | Out-Null

$healthState = Get-HealthDecision
switch ($healthState) {
    "health_ok" { }
    "scan_degraded" { Write-Log "Backend reachable but scan health degraded; deferring to scan decision" }
    default {
        Write-Log "Backend health failed ($healthState); restarting backend"
        Restart-Backend
        $healthState = Get-HealthDecision
        if ($healthState -ne "health_ok" -and $healthState -ne "scan_degraded") {
            Write-Log "ERROR backend health still failed after restart: $healthState"
            exit 1
        }
    }
}

$scan = Get-Json "$BackendUrl/api/scan/status"
$decision = Get-ScanDecision $scan
Write-Log "Scan decision: $decision"
$pokeAutoEntry = $true

switch ($decision) {
    { $_ -in @("scan_stuck", "scan_high_error_rate") } {
        $marker = Get-ScanMarker $scan
        if (Test-RestartAllowed $decision $marker) {
            Write-Log "Scan unhealthy ($decision); restarting backend"
            Restart-Backend
            if (-not $DryRun) { Save-RestartMarker $decision $marker }
        } else {
            Write-Log "Scan unhealthy ($decision) but restart suppressed for marker=$marker cooldown=${RestartCooldownMin}m"
        }
        $pokeAutoEntry = $false
    }
    { $_ -in @("scan_missing", "scan_stale", "scan_failed", "scan_unreadable") } {
        if (-not (Invoke-ScanTrigger)) { Write-Log "Scan trigger failed" }
        $pokeAutoEntry = $false
    }
    "scan_running" { $pokeAutoEntry = $false }
    "scan_ok" { }
    default { Write-Log "Unknown scan decision: $decision"; $pokeAutoEntry = $false }
}

if ($pokeAutoEntry) { Invoke-AutoEntryIfSafe }
else { Write-Log "Auto-entry poke skipped while scan state is $decision" }
Write-Log "Watchdog tick complete"
