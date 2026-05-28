$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Archive = Join-Path $PSScriptRoot "etf_mobile_pull.tar.gz"
$ShaFile = Join-Path $PSScriptRoot "etf_mobile_pull.sha256"

if (Test-Path $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}

Push-Location $Root
try {
    tar -czf $Archive `
        --exclude=etf_mobile/__pycache__ `
        --exclude=etf_mobile/**/*.pyc `
        --exclude=etf_mobile/logs/*.log `
        --exclude=etf_mobile/logs/*.log.1 `
        --exclude=etf_mobile/data/*/ohlc.csv `
        --exclude=etf_mobile/data/*/paper_state_atr.json `
        --exclude=etf_mobile/data/*/paper_history.csv `
        --exclude=etf_mobile/data/*/mobile_runner_state.json `
        --exclude=etf_mobile/data_daytrade/*/paper_state_daytrade.json `
        --exclude=etf_mobile/data_daytrade/*/paper_history_daytrade.csv `
        --exclude=etf_mobile/data_daytrade/*/mobile_runner_state_daytrade.json `
        --exclude=etf_mobile/data_daytrade/*/alerts_daytrade.csv `
        --exclude=etf_mobile/data_daytrade/order_queue.jsonl `
        --exclude=etf_mobile/data_daytrade/order_events.jsonl `
        --exclude=etf_mobile/data_daytrade/feed_status.json `
        --exclude=etf_mobile/data_daytrade/runner_status.json `
        --exclude=etf_mobile/data_daytrade/screener_results.json `
        --exclude=etf_mobile/data_daytrade/screener_schedule_state.json `
        --exclude=etf_mobile/logs/daytrade_feed.log `
        --exclude=etf_mobile/data_daytrade/daytrade.sqlite `
        etf_mobile
}
finally {
    Pop-Location
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
$name = Split-Path -Leaf $Archive
"$hash  $name" | Set-Content -LiteralPath $ShaFile -Encoding ascii

$item = Get-Item -LiteralPath $Archive
Write-Host "published archive=$Archive bytes=$($item.Length) sha=$hash"
