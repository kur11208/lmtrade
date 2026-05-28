[CmdletBinding()]
param(
    [string]$PhoneHost = $env:ETF_PHONE_HOST,
    [int]$PhonePort = $(if ($env:ETF_PHONE_PORT) { [int]$env:ETF_PHONE_PORT } else { 8022 }),
    [string]$PhoneUser = $env:ETF_PHONE_USER,
    [string]$IdentityFile = $env:ETF_PHONE_KEY,
    [switch]$IncludeLiveData,
    [switch]$Restart,
    [switch]$Watch,
    [int]$WatchSeconds = 5,
    [switch]$NoKey,
    [switch]$NoHealthCheck
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ParentDir = Split-Path -Parent $ProjectDir
$ArchiveName = "etf_mobile_deploy.tar.gz"
$ArchivePath = Join-Path $env:TEMP $ArchiveName
$UserIdentityFile = Join-Path $env:USERPROFILE ".ssh\etf_mobile_phone_ed25519"
$WorkspaceIdentityFile = Join-Path $ParentDir ".phone_ssh\etf_mobile_phone_ed25519"
if ($NoKey) {
    $IdentityFile = ""
}
elseif (-not $IdentityFile) {
    if (Test-Path $UserIdentityFile) {
        $IdentityFile = $UserIdentityFile
    }
    elseif (Test-Path $WorkspaceIdentityFile) {
        $IdentityFile = $WorkspaceIdentityFile
    }
}

function Require-Command($Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found in PATH"
    }
}

function Invoke-NativeChecked($Label, $FilePath, $Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Get-Remote() {
    if (-not $PhoneHost) {
        throw "PhoneHost is required. Example: `$env:ETF_PHONE_HOST='192.168.0.62'"
    }
    if (-not $PhoneUser) {
        throw "PhoneUser is required. Run 'whoami' in Termux, then set `$env:ETF_PHONE_USER."
    }
    return "$PhoneUser@$PhoneHost"
}

function Get-SshArgs($Remote, $Command = $null) {
    $args = @(
        "-p", "$PhonePort",
        "-o", "ConnectTimeout=8",
        "-o", "StrictHostKeyChecking=accept-new"
    )
    if ($NoKey) {
        $args += @("-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive")
    }
    if ($IdentityFile) {
        $args += @("-i", $IdentityFile, "-o", "IdentitiesOnly=yes")
    }
    $args += $Remote
    if ($Command) {
        $args += $Command
    }
    return $args
}

function Get-ScpArgs($Source, $Target) {
    $args = @(
        "-P", "$PhonePort",
        "-o", "ConnectTimeout=8",
        "-o", "StrictHostKeyChecking=accept-new"
    )
    if ($NoKey) {
        $args += @("-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive")
    }
    if ($IdentityFile) {
        $args += @("-i", $IdentityFile, "-o", "IdentitiesOnly=yes")
    }
    $args += @($Source, $Target)
    return $args
}

function Get-ExcludedPath($FullName) {
    $rel = [IO.Path]::GetRelativePath($ProjectDir, $FullName).Replace("\", "/")
    if ($rel -eq "__pycache__" -or $rel.StartsWith("__pycache__/")) { return $true }
    if ($rel.EndsWith(".pyc")) { return $true }
    if ($rel.StartsWith("logs/") -and $rel -ne "logs/optimization_results.json") { return $true }
    if (-not $IncludeLiveData) {
        if ($rel -match "^data/[^/]+/(paper_state_atr\.json|paper_history\.csv|mobile_runner_state\.json)$") { return $true }
        if ($rel -match "^data/[^/]+/ohlc\.csv$") { return $true }
        if ($rel -match "^data_daytrade/[^/]+/(paper_state_daytrade\.json|paper_history_daytrade\.csv|mobile_runner_state_daytrade\.json|alerts_daytrade\.csv)$") { return $true }
        if ($rel -match "^data_daytrade/(order_queue\.jsonl|order_events\.jsonl|feed_status\.json|runner_status\.json)$") { return $true }
        if ($rel -eq "data_daytrade/daytrade.sqlite") { return $true }
    }
    return $false
}

function Get-DeployFingerprint() {
    $items = Get-ChildItem -LiteralPath $ProjectDir -Recurse -File |
        Where-Object { -not (Get-ExcludedPath $_.FullName) } |
        Sort-Object FullName |
        ForEach-Object {
            $rel = [IO.Path]::GetRelativePath($ProjectDir, $_.FullName).Replace("\", "/")
            "$rel|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)"
        }
    $text = [string]::Join("`n", $items)
    $bytes = [Text.Encoding]::UTF8.GetBytes($text)
    $sha = [Security.Cryptography.SHA256]::Create()
    return [Convert]::ToHexString($sha.ComputeHash($bytes))
}

function Test-TcpPort($HostName, $Port, $TimeoutMs = 3000) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        $client.EndConnect($pending)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Test-PhonePreflight() {
    $sshOpen = Test-TcpPort $PhoneHost $PhonePort
    if ($sshOpen) {
        return
    }

    $appOpen = Test-TcpPort $PhoneHost 8000
    $daytradeOpen = Test-TcpPort $PhoneHost 8010
    if ($appOpen -or $daytradeOpen) {
        throw "SSH port $PhonePort is not reachable, but an app port is reachable. Start Termux sshd: pkg install openssh; passwd; sshd"
    }

    throw "Phone $PhoneHost is not reachable on ports 8000, 8010, or $PhonePort. Check phone Wi-Fi/IP, keep Termux awake, then run: ip addr show wlan0; sshd"
}

function New-DeployArchive() {
    if (Test-Path $ArchivePath) {
        Remove-Item -LiteralPath $ArchivePath -Force
    }

    $excludes = @(
        "--exclude=etf_mobile/__pycache__",
        "--exclude=etf_mobile/**/*.pyc",
        "--exclude=etf_mobile/logs/*.log",
        "--exclude=etf_mobile/logs/*.log.1",
        "--exclude=etf_mobile/logs/watchdog.log",
        "--exclude=etf_mobile/logs/supervisor.log",
        "--exclude=etf_mobile/logs/supervisor_launcher.log",
        "--exclude=etf_mobile/logs/watchdog_launcher.log",
        "--exclude=etf_mobile/logs/daytrade_app.log",
        "--exclude=etf_mobile/logs/daytrade_runner.log",
        "--exclude=etf_mobile/logs/daytrade_feed.log",
        "--exclude=etf_mobile/logs/watchdog_daytrade.log",
        "--exclude=etf_mobile/logs/watchdog_daytrade_launcher.log"
    )
    if (-not $IncludeLiveData) {
        $excludes += @(
            "--exclude=etf_mobile/data/*/paper_state_atr.json",
            "--exclude=etf_mobile/data/*/paper_history.csv",
            "--exclude=etf_mobile/data/*/mobile_runner_state.json",
            "--exclude=etf_mobile/data/*/ohlc.csv",
            "--exclude=etf_mobile/data_daytrade/*/paper_state_daytrade.json",
            "--exclude=etf_mobile/data_daytrade/*/paper_history_daytrade.csv",
            "--exclude=etf_mobile/data_daytrade/*/mobile_runner_state_daytrade.json",
            "--exclude=etf_mobile/data_daytrade/*/alerts_daytrade.csv",
            "--exclude=etf_mobile/data_daytrade/order_queue.jsonl",
            "--exclude=etf_mobile/data_daytrade/order_events.jsonl",
            "--exclude=etf_mobile/data_daytrade/feed_status.json",
            "--exclude=etf_mobile/data_daytrade/runner_status.json",
            "--exclude=etf_mobile/data_daytrade/daytrade.sqlite"
        )
    }

    Push-Location $ParentDir
    try {
        & tar -czf $ArchivePath @excludes etf_mobile
    }
    finally {
        Pop-Location
    }
}

function Invoke-DeployOnce() {
    Require-Command tar
    Require-Command ssh
    Require-Command scp

    $remote = Get-Remote
    Test-PhonePreflight
    New-DeployArchive

    Write-Host "deploy archive: $ArchivePath"
    Invoke-NativeChecked "scp archive" scp (Get-ScpArgs $ArchivePath "$remote`:~/$ArchiveName")

    $compileFiles = @(
        "app_pc_multi.py",
        "runner_pc_multi.py",
        "set_demo_mode.py",
        "daytrade_app.py",
        "daytrade_runner.py",
        "daytrade_market_calendar.py",
        "daytrade_market_data.py",
        "daytrade_status.py",
        "daytrade_view_model.py",
        "daytrade_config.py",
        "daytrade_order_queue.py",
        "daytrade_order_paper.py",
        "daytrade_feed_api_stub.py",
        "daytrade_screener.py",
        "daytrade_backtest.py",
        "daytrade_demo_feed.py",
        "daytrade_import_csv.py",
        "daytrade_sqlite.py",
        "daytrade_alerts.py"
    ) -join " "
    $remoteCommand = "tar -xzf ~/$ArchiveName -C ~ && mkdir -p ~/etf_mobile/logs && cd ~/etf_mobile && python -m py_compile $compileFiles && python runner_pc_multi.py --backfill-history && echo remote_app_updated=1"
    Invoke-NativeChecked "ssh extract" ssh (Get-SshArgs $remote $remoteCommand)

    if ($Restart) {
        $restartCommand = "cd ~/etf_mobile && pkill -f [w]atchdog_supervisor.sh 2>/dev/null || true; pkill -f [w]atchdog_etf.sh 2>/dev/null || true; pkill -f [w]atchdog_daytrade.sh 2>/dev/null || true; pkill -f [a]pp_pc_multi.py 2>/dev/null || true; pkill -f [r]unner_pc_multi.py 2>/dev/null || true; pkill -f [d]aytrade_app.py 2>/dev/null || true; pkill -f [d]aytrade_runner.py 2>/dev/null || true; pkill -f [d]aytrade_demo_feed.py 2>/dev/null || true; pkill -f '[d]aytrade_screener.py --loop' 2>/dev/null || true; nohup sh watchdog_supervisor.sh >> logs/supervisor_launcher.log 2>&1 &"
        Invoke-NativeChecked "ssh restart" ssh (Get-SshArgs $remote $restartCommand)
        Start-Sleep -Seconds 4
    }

    if (-not $NoHealthCheck) {
        try {
            $r = Invoke-WebRequest -Uri "http://$PhoneHost`:8000/" -UseBasicParsing -TimeoutSec 8
            Write-Host "health: status=$($r.StatusCode) length=$($r.Content.Length)"
        }
        catch {
            Write-Host "health check failed: $($_.Exception.Message)"
        }
        try {
            $r = Invoke-WebRequest -Uri "http://$PhoneHost`:8010/" -UseBasicParsing -TimeoutSec 8
            Write-Host "daytrade health: status=$($r.StatusCode) length=$($r.Content.Length)"
        }
        catch {
            Write-Host "daytrade health check failed: $($_.Exception.Message)"
        }
    }
}

function Invoke-WatchDeploy() {
    $last = ""
    Write-Host "watching $ProjectDir every $WatchSeconds sec"
    while ($true) {
        $current = Get-DeployFingerprint
        if ($current -ne $last) {
            if ($last) {
                Write-Host "change detected: deploying"
                Invoke-DeployOnce
            }
            else {
                Write-Host "initial fingerprint: $current"
            }
            $last = $current
        }
        Start-Sleep -Seconds $WatchSeconds
    }
}

if ($Watch) {
    Invoke-WatchDeploy
}
else {
    Invoke-DeployOnce
}
