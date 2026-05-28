param(
    [switch]$Shutdown,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Logs = Join-Path $Root "logs"
$DataDir = Join-Path $Root "data_daytrade"
$StopLog = Join-Path $Logs "market_feed_stop.log"
$AutoPowerFlag = Join-Path $DataDir "auto_power_session.json"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Write-FeedLog {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$ts] $Message" | Add-Content -LiteralPath $StopLog -Encoding UTF8
}

Write-FeedLog "market feed stop requested shutdown=$Shutdown"

function Test-AutoPowerFlag {
    if (-not (Test-Path -LiteralPath $AutoPowerFlag)) {
        Write-FeedLog "auto power flag missing"
        return $false
    }
    try {
        $payload = Get-Content -LiteralPath $AutoPowerFlag -Raw -Encoding UTF8 | ConvertFrom-Json
        if ("$($payload.date)" -ne (Get-Date -Format "yyyy-MM-dd")) {
            Write-FeedLog "auto power flag date mismatch date=$($payload.date)"
            return $false
        }
        Write-FeedLog "auto power flag accepted source=$($payload.source)"
        return $true
    } catch {
        Write-FeedLog "auto power flag invalid: $($_.Exception.Message)"
        return $false
    }
}

$mayStop = $Force -or (Test-AutoPowerFlag)
if (-not $mayStop) {
    Write-FeedLog "stop skipped because this is not an auto-power session"
    return
}

$bridgeProcesses = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'pwsh.exe'" |
    Where-Object { $_.CommandLine -like "*rakuten_rss_bridge.ps1*" }
foreach ($proc in $bridgeProcesses) {
    Write-FeedLog "stopping RSS bridge pid=$($proc.ProcessId)"
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

foreach ($proc in Get-Process -Name MarketSpeed2 -ErrorAction SilentlyContinue) {
    Write-FeedLog "stopping MarketSpeed II pid=$($proc.Id)"
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}

Write-FeedLog "market feed stop completed"
Remove-Item -LiteralPath $AutoPowerFlag -Force -ErrorAction SilentlyContinue

if ($Shutdown) {
    Write-FeedLog "shutdown requested"
    shutdown.exe /s /t 60 /c "LMTrade market feed finished. Shutting down." /d p:0:0
}
