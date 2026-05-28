param(
    [switch]$AutoPowerSession,
    [switch]$CheckPhoneWake,
    [int]$PhoneWakeValidMinutes = 120
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Logs = Join-Path $Root "logs"
$DataDir = Join-Path $Root "data_daytrade"
$MarketSpeedPath = Join-Path $env:LOCALAPPDATA "MarketSpeed2\Bin\MarketSpeed2.exe"
$BridgeScript = Join-Path $Root "rakuten_rss_bridge.ps1"
$BridgeWorkbook = Join-Path $Root "data_daytrade\rakuten_rss_bridge_loop.xlsx"
$BridgeLog = Join-Path $Logs "rakuten_rss_bridge.log"
$BridgeErrLog = Join-Path $Logs "rakuten_rss_bridge.err.log"
$StartLog = Join-Path $Logs "market_feed_start.log"
$AutoPowerFlag = Join-Path $DataDir "auto_power_session.json"
$PushUrl = "http://192.168.0.96:8010/feed/bars"
$PhoneWakeUrl = "http://192.168.0.96:8010/pc-wake.json"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

function Write-FeedLog {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$ts] $Message" | Add-Content -LiteralPath $StartLog -Encoding UTF8
}

function Stop-StaleBridge {
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'pwsh.exe'" |
        Where-Object { $_.CommandLine -like "*rakuten_rss_bridge.ps1*" }
    foreach ($proc in $processes) {
        Write-FeedLog "stopping stale bridge pid=$($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Write-AutoPowerFlag {
    param([string]$Source)
    $payload = [ordered]@{
        ok = $true
        date = (Get-Date -Format "yyyy-MM-dd")
        started_at = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        source = $Source
    }
    $json = $payload | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($AutoPowerFlag, $json, [System.Text.UTF8Encoding]::new($false))
    Write-FeedLog "auto power flag set source=$Source"
}

function Test-RecentPhoneWake {
    try {
        $payload = Invoke-RestMethod -Uri $PhoneWakeUrl -TimeoutSec 5
        if (-not $payload.ok) {
            Write-FeedLog "phone wake flag unavailable"
            return $false
        }
        if ("$($payload.date)" -ne (Get-Date -Format "yyyy-MM-dd")) {
            Write-FeedLog "phone wake flag not for today date=$($payload.date)"
            return $false
        }
        $wakeAt = [datetime]::ParseExact("$($payload.wake_sent_at)", "yyyy-MM-dd HH:mm:ss", $null)
        $ageMinutes = ((Get-Date) - $wakeAt).TotalMinutes
        if ($ageMinutes -lt 0 -or $ageMinutes -gt $PhoneWakeValidMinutes) {
            Write-FeedLog ("phone wake flag too old age_minutes={0:n1}" -f $ageMinutes)
            return $false
        }
        Write-FeedLog ("phone wake flag accepted age_minutes={0:n1}" -f $ageMinutes)
        return $true
    } catch {
        Write-FeedLog "phone wake flag check failed: $($_.Exception.Message)"
        return $false
    }
}

Write-FeedLog "market feed start requested auto_power=$AutoPowerSession check_phone_wake=$CheckPhoneWake"

if ($AutoPowerSession) {
    Write-AutoPowerFlag -Source "explicit"
} elseif ($CheckPhoneWake -and (Test-RecentPhoneWake)) {
    Write-AutoPowerFlag -Source "phone_wake"
} else {
    Write-FeedLog "auto power flag not set"
}

if (-not (Test-Path -LiteralPath $MarketSpeedPath)) {
    throw "MarketSpeed II executable not found: $MarketSpeedPath"
}
if (-not (Test-Path -LiteralPath $BridgeScript)) {
    throw "RSS bridge script not found: $BridgeScript"
}

Stop-StaleBridge

if (-not (Get-Process -Name MarketSpeed2 -ErrorAction SilentlyContinue)) {
    Write-FeedLog "starting MarketSpeed II"
    Start-Process -FilePath $MarketSpeedPath -WorkingDirectory (Split-Path -Parent $MarketSpeedPath)
    Start-Sleep -Seconds 45
} else {
    Write-FeedLog "MarketSpeed II already running"
}

$args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $BridgeScript,
    "-EnabledOnly",
    "-Loop",
    "-RunRunner",
    "-PushUrl", $PushUrl,
    "-WorkbookPath", $BridgeWorkbook,
    "-PollSeconds", "5",
    "-ConnectTimeoutSeconds", "90"
)

Write-FeedLog "starting RSS bridge push_url=$PushUrl"
Start-Process `
    -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList $args `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $BridgeLog `
    -RedirectStandardError $BridgeErrLog `
    -WindowStyle Hidden | Out-Null

Write-FeedLog "market feed start completed"
