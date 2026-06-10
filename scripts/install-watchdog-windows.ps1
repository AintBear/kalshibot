# Registers the kalshibot watchdog as a Windows Task Scheduler job.
# Equivalent of scripts/install-watchdog-launchd.sh on the Mac.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-watchdog-windows.ps1
#
# Creates task "KalshibotWatchdog": runs scripts\watchdog.ps1 every 5 minutes
# as the current user, hidden window. Uses schtasks.exe — Register-ScheduledTask
# is access-denied for non-elevated users on some Windows 11 configurations.
# Remove with:  schtasks /Delete /TN KalshibotWatchdog /F

$ErrorActionPreference = "Stop"
$TaskName = "KalshibotWatchdog"
$RepoDir = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $RepoDir "scripts\watchdog.ps1"

if (-not (Test-Path $Script)) { throw "watchdog.ps1 not found at $Script" }

$cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $Script"
schtasks /Create /TN $TaskName /TR $cmd /SC MINUTE /MO 5 /F
if ($LASTEXITCODE -ne 0) { throw "schtasks /Create failed with exit code $LASTEXITCODE" }

Write-Output "Registered task '$TaskName' (every 5 min)."
Write-Output "Logs: $RepoDir\logs\watchdog.log"
Write-Output "Dry-run test: powershell -ExecutionPolicy Bypass -File `"$Script`" -DryRun"
Write-Output "Run now:      schtasks /Run /TN $TaskName"
Write-Output "Remove:       schtasks /Delete /TN $TaskName /F"
