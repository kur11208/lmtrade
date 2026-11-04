# Publish only committed source files, not the working tree or local credentials.
# Configure the same randomly generated secret on the PC and phone; never commit it.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Archive = Join-Path $PSScriptRoot "etf_mobile_pull.tar.gz"
$ShaFile = Join-Path $PSScriptRoot "etf_mobile_pull.sha256"
$SigFile = Join-Path $PSScriptRoot "etf_mobile_pull.sha256.hmac"

$HmacKey = $env:LMTRADE_UPDATE_HMAC_KEY
if ([string]::IsNullOrWhiteSpace($HmacKey) -or $HmacKey.Length -lt 32) {
    throw "LMTRADE_UPDATE_HMAC_KEY must be a private, random secret of at least 32 characters."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required for a safe, source-only release."
}
& git -C $Root rev-parse --verify HEAD *> $null
if ($LASTEXITCODE -ne 0) { throw "Not a valid Git checkout." }

# git archive never packages untracked runtime databases, secrets or .env files.
# Deploy only the vetted, committed etf_mobile tree.
& git -C $Root archive --format=tar.gz --output=$Archive HEAD etf_mobile
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Archive)) {
    throw "git archive failed; no update was published."
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
$manifest = "$hash  etf_mobile_pull.tar.gz`n"
$utf8 = [System.Text.UTF8Encoding]::new($false)
$bytes = $utf8.GetBytes($manifest)
$hmac = [System.Security.Cryptography.HMACSHA256]::new($utf8.GetBytes($HmacKey))
try {
    $signature = [BitConverter]::ToString($hmac.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
} finally {
    $hmac.Dispose()
}
[System.IO.File]::WriteAllBytes($ShaFile, $bytes)
[System.IO.File]::WriteAllText($SigFile, $signature + "`n", $utf8)
$item = Get-Item -LiteralPath $Archive
Write-Host "published committed code archive=$Archive bytes=$($item.Length) sha256=$hash (HMAC signed)"
