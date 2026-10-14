<#
.SYNOPSIS
  Start YardLens with Windows: registers two scheduled tasks (API and dashboard) that run at boot,
  below normal priority, and restart if they stop.

.DESCRIPTION
  Run once from an elevated PowerShell after `bun run setup` and `bun run build`:

    powershell -ExecutionPolicy Bypass -File deploy\windows\register-tasks.ps1

  The tasks run as the current user whether or not anyone is signed in. Remove them with
  -Unregister. Logs go to apps\api\logs and apps\web\logs.
#>
param(
  [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$names = @("YardLens API", "YardLens Web")

if ($Unregister) {
  foreach ($name in $names) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
  }
  Write-Host "Removed the YardLens tasks."
  return
}

$uv = (Get-Command uv -ErrorAction Stop).Source
$bun = (Get-Command bun -ErrorAction Stop).Source

function Register-YardLensTask([string]$Name, [string]$Directory, [string]$Command) {
  $logs = Join-Path $Directory "logs"
  New-Item -ItemType Directory -Force -Path $logs | Out-Null
  # cmd.exe only to redirect output into a log file.
  $action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c $Command >> `"$logs\service.log`" 2>&1" -WorkingDirectory $Directory
  $trigger = New-ScheduledTaskTrigger -AtStartup
  $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
  # Priority 7 is "below normal"; no time limit; restart every minute if it stops.
  $settings = New-ScheduledTaskSettingsSet -Priority 7 -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
  Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force | Out-Null
  Write-Host "Registered '$Name' ($Directory)."
}

Register-YardLensTask $names[0] (Join-Path $root "apps\api") "`"$uv`" run --no-sync yardlens-api"
Register-YardLensTask $names[1] (Join-Path $root "apps\web") "`"$bun`" run start"
Write-Host "Start them now with: Start-ScheduledTask -TaskName 'YardLens API'; Start-ScheduledTask -TaskName 'YardLens Web'"
