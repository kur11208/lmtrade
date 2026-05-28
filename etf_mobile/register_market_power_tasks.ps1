$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScript = Join-Path $Root "market_feed_start.ps1"
$StopScript = Join-Path $Root "market_feed_stop.ps1"
$PowerShell = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path -LiteralPath $StartScript)) {
    throw "start script not found: $StartScript"
}
if (-not (Test-Path -LiteralPath $StopScript)) {
    throw "stop script not found: $StopScript"
}

$startAction = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -CheckPhoneWake"
$startTriggers = @(
    (New-ScheduledTaskTrigger -AtLogOn),
    (New-ScheduledTaskTrigger -Daily -At "08:55")
)
$startSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)
Register-ScheduledTask `
    -TaskName "LMTrade Market Feed Start" `
    -Action $startAction `
    -Trigger $startTriggers `
    -Settings $startSettings `
    -Description "Start MarketSpeed II and Rakuten RSS bridge for LMTrade." `
    -Force | Out-Null

$stopAction = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StopScript`" -Shutdown"
$stopTrigger = New-ScheduledTaskTrigger -Daily -At "15:45"
$stopSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask `
    -TaskName "LMTrade Market Feed Stop and Shutdown" `
    -Action $stopAction `
    -Trigger $stopTrigger `
    -Settings $stopSettings `
    -Description "Stop LMTrade market feed and shut down the PC after market close." `
    -Force | Out-Null

Write-Host "registered tasks:"
Get-ScheduledTask -TaskName "LMTrade Market Feed Start", "LMTrade Market Feed Stop and Shutdown" |
    Select-Object TaskName, State
