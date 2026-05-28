param(
    [string[]]$Symbols = @(),
    [switch]$EnabledOnly,
    [switch]$Visible,
    [switch]$KeepWorkbook,
    [switch]$Loop,
    [switch]$RunRunner,
    [int]$PollSeconds = 5,
    [int]$BarMinutes = 1,
    [int]$ConnectTimeoutSeconds = 45,
    [string]$WorkbookPath = "",
    [string]$PushUrl = "",
    [string]$PushToken = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $ScriptDir "data_daytrade"
$SymbolsFile = Join-Path $DataDir "symbols.json"
$FeedStatusFile = Join-Path $DataDir "feed_status.json"
$StateFile = Join-Path $DataDir "rakuten_rss_state.json"
if ([string]::IsNullOrWhiteSpace($WorkbookPath)) {
    $WorkbookPath = Join-Path $DataDir "rakuten_rss_bridge.xlsx"
}
if (-not [System.IO.Path]::IsPathRooted($WorkbookPath)) {
    $WorkbookPath = Join-Path $ScriptDir $WorkbookPath
}

function New-Utf8NoBomEncoding {
    return New-Object System.Text.UTF8Encoding($false)
}

function ConvertTo-LocalIso {
    param([datetime]$Value)
    return $Value.ToString("yyyy-MM-dd HH:mm:ss")
}

function ConvertTo-UtcIso {
    return [datetime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss+00:00")
}

function Write-JsonAtomic {
    param(
        [string]$Path,
        [object]$Value
    )
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $tmp = "$Path.tmp"
    $json = $Value | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($tmp, $json, (New-Utf8NoBomEncoding))
    Move-Item -Force -LiteralPath $tmp -Destination $Path
}

function Write-FeedStatus {
    param(
        [string]$Status,
        [string]$Message = "",
        [string[]]$StatusSymbols = @(),
        [int]$BarsWritten = 0,
        [string]$LastBarTs = "",
        [string]$ErrorText = "",
        [hashtable]$Extra = @{}
    )
    $payload = [ordered]@{
        kind = "feed"
        adapter = "rakuten_rss"
        status = $Status
        message = $Message
        symbols = @($StatusSymbols)
        bars_written = $BarsWritten
        last_bar_ts = $LastBarTs
        last_feed_at = (ConvertTo-LocalIso -Value ([datetime]::Now))
        last_feed_at_utc = (ConvertTo-UtcIso)
        last_error = $ErrorText
    }
    foreach ($key in $Extra.Keys) {
        $payload[$key] = $Extra[$key]
    }
    Write-JsonAtomic -Path $FeedStatusFile -Value $payload
}

function Read-JsonHashtable {
    param([string]$Path)
    $table = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $table
    }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return $table
        }
        $obj = $raw | ConvertFrom-Json
        foreach ($prop in $obj.PSObject.Properties) {
            $table[$prop.Name] = $prop.Value
        }
    } catch {
        return @{}
    }
    return $table
}

function Get-BridgeSymbols {
    if ($Symbols.Count -gt 0) {
        return @($Symbols | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    }
    if (-not (Test-Path -LiteralPath $SymbolsFile)) {
        throw "symbols file not found: $SymbolsFile"
    }
    $items = Get-Content -LiteralPath $SymbolsFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $selected = @()
    foreach ($item in $items) {
        $candidate = $true
        if ($null -ne $item.candidate) {
            $candidate = [bool]$item.candidate
        }
        $enabled = $true
        if ($null -ne $item.enabled) {
            $enabled = [bool]$item.enabled
        }
        if ($candidate -and ((-not $EnabledOnly) -or $enabled)) {
            $selected += "$($item.symbol)"
        }
    }
    return @($selected)
}

function New-JpText {
    param([int[]]$CodePoints)
    return -join ($CodePoints | ForEach-Object { [char]$_ })
}

function Get-RssItems {
    return [ordered]@{
        Name = (New-JpText @(0x9298, 0x67C4, 0x540D, 0x79F0))
        CurrentDate = (New-JpText @(0x73FE, 0x5728, 0x65E5, 0x4ED8))
        CurrentTime = (New-JpText @(0x73FE, 0x5728, 0x5024, 0x8A73, 0x7D30, 0x6642, 0x523B))
        CurrentPrice = (New-JpText @(0x73FE, 0x5728, 0x5024))
        DayOpen = (New-JpText @(0x59CB, 0x5024))
        DayHigh = (New-JpText @(0x9AD8, 0x5024))
        DayLow = (New-JpText @(0x5B89, 0x5024))
        Volume = (New-JpText @(0x51FA, 0x6765, 0x9AD8))
    }
}

function Get-RssXllPath {
    $base = Join-Path $env:LOCALAPPDATA "MarketSpeed2\Bin\rss"
    $xll64 = Join-Path $base "MarketSpeed2_RSS_64bit.xll"
    $xll32 = Join-Path $base "MarketSpeed2_RSS_32bit.xll"
    if (Test-Path -LiteralPath $xll64) {
        return $xll64
    }
    if (Test-Path -LiteralPath $xll32) {
        return $xll32
    }
    throw "MarketSpeed II RSS XLL was not found under $base"
}

function Get-RssXlamPath {
    $path = Join-Path $env:LOCALAPPDATA "MarketSpeed2\Bin\rss\MarketSpeed2_RSS_VBA.xlam"
    if (Test-Path -LiteralPath $path) {
        return $path
    }
    return ""
}

function Register-RssAddIn {
    param(
        [object]$Excel,
        [string]$Path
    )
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    try {
        $addin = $null
        foreach ($item in @($Excel.AddIns)) {
            try {
                if ("$($item.FullName)" -ieq $Path) {
                    $addin = $item
                    break
                }
            } catch {
            }
        }
        if ($null -eq $addin) {
            $addin = $Excel.AddIns.Add($Path, $false)
        }
        if ($null -ne $addin) {
            $addin.Installed = $true
            return $true
        }
    } catch {
        Write-Host "Excel add-in registration failed for $Path : $($_.Exception.Message)"
    }
    return $false
}

function ConvertTo-RssSymbol {
    param([string]$Symbol)
    $text = "$Symbol".Trim()
    if ($text -match "\.") {
        return $text
    }
    return "$text.T"
}

function Set-CellFormula {
    param(
        [object]$Sheet,
        [int]$Row,
        [int]$Column,
        [string]$ItemName
    )
    $Sheet.Cells.Item($Row, $Column).Formula = '=RssMarket($A' + $Row + ',"' + $ItemName + '")'
}

function Set-CellDirectFormula {
    param(
        [object]$Sheet,
        [int]$Row,
        [int]$Column,
        [string]$Symbol,
        [string]$ItemName
    )
    $Sheet.Cells.Item($Row, $Column).Formula = '=RssMarket("' + $Symbol + '","' + $ItemName + '")'
}

function New-RssWorkbook {
    param(
        [object]$Excel,
        [string[]]$BridgeSymbols,
        [string]$Path
    )
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }

    $items = Get-RssItems
    $wb = $Excel.Workbooks.Add()
    $ws = $wb.Worksheets.Item(1)
    $ws.Name = "rss"
    $headers = @("symbol", "name", "rss_date", "rss_time", "price", "day_open", "day_high", "day_low", "volume")
    for ($i = 0; $i -lt $headers.Count; $i++) {
        $ws.Cells.Item(1, $i + 1).Value2 = $headers[$i]
    }
    for ($i = 0; $i -lt $BridgeSymbols.Count; $i++) {
        $row = $i + 2
        $rssSymbol = ConvertTo-RssSymbol -Symbol $BridgeSymbols[$i]
        $ws.Cells.Item($row, 1).Value2 = $rssSymbol
        Set-CellDirectFormula -Sheet $ws -Row $row -Column 2 -Symbol $rssSymbol -ItemName $items.Name
        Set-CellDirectFormula -Sheet $ws -Row $row -Column 3 -Symbol $rssSymbol -ItemName $items.CurrentDate
        Set-CellDirectFormula -Sheet $ws -Row $row -Column 4 -Symbol $rssSymbol -ItemName $items.CurrentTime
        Set-CellDirectFormula -Sheet $ws -Row $row -Column 5 -Symbol $rssSymbol -ItemName $items.CurrentPrice
        Set-CellDirectFormula -Sheet $ws -Row $row -Column 6 -Symbol $rssSymbol -ItemName $items.DayOpen
        Set-CellDirectFormula -Sheet $ws -Row $row -Column 7 -Symbol $rssSymbol -ItemName $items.DayHigh
        Set-CellDirectFormula -Sheet $ws -Row $row -Column 8 -Symbol $rssSymbol -ItemName $items.DayLow
        Set-CellDirectFormula -Sheet $ws -Row $row -Column 9 -Symbol $rssSymbol -ItemName $items.Volume
    }
    $ws.Columns.AutoFit() | Out-Null
    $wb.SaveAs($Path, 51)
    $wb.Close($true)
}

function Convert-ExcelNumber {
    param([object]$Value)
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [double] -or $Value -is [int] -or $Value -is [decimal]) {
        return [double]$Value
    }
    $text = "$Value".Trim()
    if ($text -eq "" -or $text.StartsWith("#")) {
        return $null
    }
    $text = $text.Replace(",", "")
    $number = 0.0
    if ([double]::TryParse($text, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$number)) {
        return $number
    }
    if ([double]::TryParse($text, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::CurrentCulture, [ref]$number)) {
        return $number
    }
    return $null
}

function Convert-ExcelDate {
    param([object]$Value)
    if ($null -eq $Value) {
        return [datetime]::Today
    }
    if ($Value -is [double] -or $Value -is [int] -or $Value -is [decimal]) {
        try {
            return [datetime]::FromOADate([double]$Value).Date
        } catch {
            return [datetime]::Today
        }
    }
    $text = "$Value".Trim()
    if ($text -eq "" -or $text.StartsWith("#")) {
        return [datetime]::Today
    }
    $date = [datetime]::MinValue
    $formats = @("yyyy/MM/dd", "yyyy-MM-dd", "yyyy/M/d", "yyyy-M-d")
    if ([datetime]::TryParseExact($text, $formats, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::None, [ref]$date)) {
        return $date.Date
    }
    if ([datetime]::TryParse($text, [ref]$date)) {
        return $date.Date
    }
    return [datetime]::Today
}

function Convert-ExcelTime {
    param([object]$Value)
    if ($null -eq $Value) {
        return [datetime]::Now.TimeOfDay
    }
    if ($Value -is [double] -or $Value -is [int] -or $Value -is [decimal]) {
        try {
            return [datetime]::FromOADate([double]$Value).TimeOfDay
        } catch {
            return [datetime]::Now.TimeOfDay
        }
    }
    $text = "$Value".Trim()
    if ($text -eq "" -or $text.StartsWith("#")) {
        return [datetime]::Now.TimeOfDay
    }
    $date = [datetime]::MinValue
    $formats = @("H:mm:ss", "HH:mm:ss", "H:mm", "HH:mm")
    if ([datetime]::TryParseExact($text, $formats, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::None, [ref]$date)) {
        return $date.TimeOfDay
    }
    if ([datetime]::TryParse($text, [ref]$date)) {
        return $date.TimeOfDay
    }
    return [datetime]::Now.TimeOfDay
}

function Get-BarTimestamp {
    param(
        [object]$DateValue,
        [object]$TimeValue,
        [int]$Minutes
    )
    $date = Convert-ExcelDate -Value $DateValue
    $time = Convert-ExcelTime -Value $TimeValue
    $ts = $date.Add($time)
    $minutesFloor = [Math]::Floor($ts.Minute / [Math]::Max(1, $Minutes)) * [Math]::Max(1, $Minutes)
    return $ts.Date.AddHours($ts.Hour).AddMinutes($minutesFloor)
}

function Get-StateValue {
    param(
        [hashtable]$State,
        [string]$Symbol,
        [string]$Name
    )
    if (-not $State.ContainsKey($Symbol)) {
        return $null
    }
    $item = $State[$Symbol]
    if ($null -eq $item) {
        return $null
    }
    return $item.PSObject.Properties[$Name].Value
}

function Set-StateValue {
    param(
        [hashtable]$State,
        [string]$Symbol,
        [datetime]$SampleTs,
        [double]$Price,
        [Nullable[double]]$CumulativeVolume
    )
    $State[$Symbol] = [ordered]@{
        last_sample_ts = (ConvertTo-LocalIso -Value $SampleTs)
        last_price = $Price
        last_cumulative_volume = $CumulativeVolume
    }
}

function Send-BarsToRemote {
    param(
        [object[]]$Bars,
        [string[]]$StatusSymbols,
        [string]$LastBarTs
    )
    if ([string]::IsNullOrWhiteSpace($PushUrl) -or $Bars.Count -eq 0) {
        return @{ enabled = -not [string]::IsNullOrWhiteSpace($PushUrl); ok = $true; posted = 0; message = "" }
    }
    try {
        $payload = [ordered]@{
            adapter = "rakuten_rss"
            symbols = @($StatusSymbols)
            bars = @($Bars)
            last_bar_ts = $LastBarTs
        }
        $json = $payload | ConvertTo-Json -Depth 8
        $headers = @{}
        if (-not [string]::IsNullOrWhiteSpace($PushToken)) {
            $headers["X-Daytrade-Feed-Token"] = $PushToken
        }
        $body = [System.Text.Encoding]::UTF8.GetBytes($json)
        $response = Invoke-RestMethod -Method Post -Uri $PushUrl -Headers $headers -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 10
        return @{
            enabled = $true
            ok = [bool]$response.ok
            posted = $Bars.Count
            accepted = $response.accepted
            appended = $response.appended
            message = "remote feed accepted"
        }
    } catch {
        return @{
            enabled = $true
            ok = $false
            posted = $Bars.Count
            message = "$($_.Exception.Message)"
        }
    }
}

function Convert-CsvNumber {
    param([object]$Value, [double]$Default = 0.0)
    $parsed = Convert-ExcelNumber -Value $Value
    if ($null -eq $parsed) {
        return $Default
    }
    return $parsed
}

function Write-IntradayBar {
    param(
        [string]$Symbol,
        [datetime]$Timestamp,
        [double]$Open,
        [double]$High,
        [double]$Low,
        [double]$Close,
        [double]$Volume
    )
    $dir = Join-Path $DataDir $Symbol
    $path = Join-Path $dir "intraday_bars.csv"
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $tsText = ConvertTo-LocalIso -Value $Timestamp
    if (-not (Test-Path -LiteralPath $path) -or (Get-Item -LiteralPath $path).Length -eq 0) {
        $line = "timestamp,open,high,low,close,volume`r`n$tsText,$Open,$High,$Low,$Close,$Volume"
        [System.IO.File]::WriteAllText($path, $line + "`r`n", (New-Utf8NoBomEncoding))
        return [ordered]@{
            appended = $true
            bar = [ordered]@{
                symbol = $Symbol
                timestamp = $tsText
                open = [Math]::Round($Open, 6)
                high = [Math]::Round($High, 6)
                low = [Math]::Round($Low, 6)
                close = [Math]::Round($Close, 6)
                volume = [Math]::Round([Math]::Max(0.0, $Volume), 0)
            }
        }
    }

    $rows = @(Import-Csv -LiteralPath $path)
    if ($rows.Count -gt 0 -and $rows[-1].timestamp -eq $tsText) {
        $last = $rows[-1]
        $existingOpen = Convert-CsvNumber -Value $last.open -Default $Open
        $existingHigh = Convert-CsvNumber -Value $last.high -Default $High
        $existingLow = Convert-CsvNumber -Value $last.low -Default $Low
        $existingVolume = Convert-CsvNumber -Value $last.volume -Default 0.0
        $last.open = [string][Math]::Round($existingOpen, 6)
        $last.high = [string][Math]::Round([Math]::Max($existingHigh, $High), 6)
        $last.low = [string][Math]::Round([Math]::Min($existingLow, $Low), 6)
        $last.close = [string][Math]::Round($Close, 6)
        $last.volume = [string][Math]::Round([Math]::Max(0.0, $existingVolume + $Volume), 0)
        $rows | Export-Csv -LiteralPath $path -NoTypeInformation -Encoding UTF8
        return [ordered]@{
            appended = $false
            bar = [ordered]@{
                symbol = $Symbol
                timestamp = $tsText
                open = [Math]::Round($existingOpen, 6)
                high = [Math]::Round([Math]::Max($existingHigh, $High), 6)
                low = [Math]::Round([Math]::Min($existingLow, $Low), 6)
                close = [Math]::Round($Close, 6)
                volume = [Math]::Round([Math]::Max(0.0, $existingVolume + $Volume), 0)
            }
        }
    }

    $line = "$tsText,$([Math]::Round($Open, 6)),$([Math]::Round($High, 6)),$([Math]::Round($Low, 6)),$([Math]::Round($Close, 6)),$([Math]::Round([Math]::Max(0.0, $Volume), 0))"
    Add-Content -LiteralPath $path -Value $line -Encoding UTF8
    return [ordered]@{
        appended = $true
        bar = [ordered]@{
            symbol = $Symbol
            timestamp = $tsText
            open = [Math]::Round($Open, 6)
            high = [Math]::Round($High, 6)
            low = [Math]::Round($Low, 6)
            close = [Math]::Round($Close, 6)
            volume = [Math]::Round([Math]::Max(0.0, $Volume), 0)
        }
    }
}

function Read-RssRows {
    param(
        [object]$Sheet,
        [string[]]$BridgeSymbols,
        [hashtable]$State
    )
    $written = 0
    $seenPrices = 0
    $lastTs = ""
    $bars = @()
    foreach ($symbol in $BridgeSymbols) {
        $index = [array]::IndexOf($BridgeSymbols, $symbol)
        $row = $index + 2
        $dateValue = $Sheet.Cells.Item($row, 3).Value2
        $timeValue = $Sheet.Cells.Item($row, 4).Value2
        $price = Convert-ExcelNumber -Value $Sheet.Cells.Item($row, 5).Value2
        $cumVolume = Convert-ExcelNumber -Value $Sheet.Cells.Item($row, 9).Value2
        if ($null -eq $price -or $price -le 0) {
            continue
        }
        $seenPrices += 1
        $barTs = Get-BarTimestamp -DateValue $dateValue -TimeValue $timeValue -Minutes $BarMinutes
        $prevPrice = Convert-ExcelNumber -Value (Get-StateValue -State $State -Symbol $symbol -Name "last_price")
        if ($null -eq $prevPrice -or $prevPrice -le 0) {
            $prevPrice = $price
        }
        $prevVolume = Convert-ExcelNumber -Value (Get-StateValue -State $State -Symbol $symbol -Name "last_cumulative_volume")
        $deltaVolume = 0.0
        if ($null -ne $cumVolume -and $null -ne $prevVolume -and $cumVolume -ge $prevVolume) {
            $deltaVolume = $cumVolume - $prevVolume
        }
        $open = [double]$prevPrice
        $high = [Math]::Max([double]$open, [double]$price)
        $low = [Math]::Min([double]$open, [double]$price)
        $writeResult = Write-IntradayBar -Symbol $symbol -Timestamp $barTs -Open $open -High $high -Low $low -Close ([double]$price) -Volume ([double]$deltaVolume)
        if ($writeResult.appended) {
            $written += 1
        }
        if ($null -ne $writeResult.bar) {
            $bars += $writeResult.bar
        }
        $lastTs = ConvertTo-LocalIso -Value $barTs
        Set-StateValue -State $State -Symbol $symbol -SampleTs $barTs -Price ([double]$price) -CumulativeVolume $cumVolume
    }
    return [ordered]@{
        seen_prices = $seenPrices
        bars_written = $written
        last_bar_ts = $lastTs
        bars = @($bars)
    }
}

function Get-RssDiagnostics {
    param(
        [object]$Excel,
        [object]$Sheet,
        [string[]]$BridgeSymbols,
        [string]$XllPath,
        [string]$XlamPath,
        [bool]$XllRegistered
    )
    $rows = @()
    foreach ($symbol in $BridgeSymbols) {
        $index = [array]::IndexOf($BridgeSymbols, $symbol)
        $row = $index + 2
        $rows += [ordered]@{
            symbol = $symbol
            rss_symbol = "$($Sheet.Cells.Item($row, 1).Text)"
            name_text = "$($Sheet.Cells.Item($row, 2).Text)"
            date_text = "$($Sheet.Cells.Item($row, 3).Text)"
            time_text = "$($Sheet.Cells.Item($row, 4).Text)"
            price_text = "$($Sheet.Cells.Item($row, 5).Text)"
            price_value = "$($Sheet.Cells.Item($row, 5).Value2)"
            price_formula = "$($Sheet.Cells.Item($row, 5).Formula)"
        }
    }
    $addins = @()
    try {
        foreach ($item in @($Excel.AddIns)) {
            try {
                $fullName = "$($item.FullName)"
                if ($fullName -like "*MarketSpeed2_RSS*") {
                    $addins += [ordered]@{
                        name = "$($item.Name)"
                        installed = [bool]$item.Installed
                        full_name = $fullName
                    }
                }
            } catch {
            }
        }
    } catch {
    }
    return @{
        xll_path = $XllPath
        xlam_path = $XlamPath
        xll_registered = $XllRegistered
        calculation = "$($Excel.Calculation)"
        rows = @($rows)
        excel_addins = @($addins)
    }
}

function Invoke-RunnerOnce {
    if (-not $RunRunner) {
        return
    }
    Push-Location $ScriptDir
    try {
        & python daytrade_runner.py --once | Out-Null
    } finally {
        Pop-Location
    }
}

$excel = $null
$workbook = $null
$rssAddinWorkbook = $null
try {
    $bridgeSymbols = Get-BridgeSymbols
    if ($bridgeSymbols.Count -eq 0) {
        throw "no symbols selected"
    }
    $marketSpeed = Get-Process -Name "MarketSpeed2" -ErrorAction SilentlyContinue
    if (-not $marketSpeed) {
        Write-FeedStatus -Status "error" -Message "MarketSpeed II is not running" -StatusSymbols $bridgeSymbols -ErrorText "MarketSpeed2 process not found"
        throw "MarketSpeed II is not running"
    }
    $xll = Get-RssXllPath
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = [bool]$Visible
    $excel.DisplayAlerts = $false
    $excel.EnableEvents = $true
    try {
        $excel.AskToUpdateLinks = $false
    } catch {
    }
    try {
        $excel.Calculation = -4105
    } catch {
        Write-Host "Excel calculation mode could not be changed: $($_.Exception.Message)"
    }
    $registered = $excel.RegisterXLL($xll)
    [void](Register-RssAddIn -Excel $excel -Path $xll)
    if (-not $registered) {
        Write-FeedStatus -Status "error" -Message "RSS XLL registration failed" -StatusSymbols $bridgeSymbols -ErrorText $xll
        throw "RSS XLL registration failed: $xll"
    }
    $xlam = Get-RssXlamPath
    if (-not [string]::IsNullOrWhiteSpace($xlam)) {
        [void](Register-RssAddIn -Excel $excel -Path $xlam)
        try {
            $rssAddinWorkbook = $excel.Workbooks.Open($xlam)
        } catch {
            Write-Host "RSS VBA add-in could not be opened: $($_.Exception.Message)"
        }
    }

    New-RssWorkbook -Excel $excel -BridgeSymbols $bridgeSymbols -Path $WorkbookPath
    $workbook = $excel.Workbooks.Open($WorkbookPath)
    $sheet = $workbook.Worksheets.Item(1)
    $state = Read-JsonHashtable -Path $StateFile

    $deadline = [datetime]::Now.AddSeconds([Math]::Max(1, $ConnectTimeoutSeconds))
    $connected = $false
    $diagnostics = @{}
    do {
        try {
            $excel.CalculateFullRebuild()
            Start-Sleep -Seconds 2
            $sample = Read-RssRows -Sheet $sheet -BridgeSymbols $bridgeSymbols -State $state
            if ($sample.seen_prices -gt 0) {
                $connected = $true
                break
            }
        } catch {
            Write-Host ("{0} rss connect retry after Excel COM error: {1}" -f (ConvertTo-LocalIso -Value ([datetime]::Now)), $_.Exception.Message)
            Start-Sleep -Seconds 2
        }
    } while ([datetime]::Now -lt $deadline)

    if (-not $connected) {
        $diagnostics = Get-RssDiagnostics -Excel $excel -Sheet $sheet -BridgeSymbols $bridgeSymbols -XllPath $xll -XlamPath $xlam -XllRegistered ([bool]$registered)
        Write-FeedStatus -Status "empty" -Message "RSS returned no prices. Open the workbook visibly and press the MarketSpeed II RSS connect button or complete the RSS agreement." -StatusSymbols $bridgeSymbols -Extra @{ diagnostics = $diagnostics }
        if ($Visible -or $KeepWorkbook) {
            Write-Host "RSS returned no prices. Leave this Excel window open, connect MarketSpeed II RSS, then run the bridge again."
            return
        }
        Write-Host "RSS returned no prices"
        return
    }

    while ($true) {
        try {
            $excel.CalculateFullRebuild()
            Start-Sleep -Seconds 1
            $result = Read-RssRows -Sheet $sheet -BridgeSymbols $bridgeSymbols -State $state
        } catch {
            $errorText = "$($_.Exception.Message)"
            Write-FeedStatus -Status "error" -Message "rss sampling failed; retrying" -StatusSymbols $bridgeSymbols -ErrorText $errorText
            Write-Host ("{0} status=error rss sampling retry error={1}" -f (ConvertTo-LocalIso -Value ([datetime]::Now)), $errorText)
            if (-not $Loop) {
                throw
            }
            Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
            continue
        }
        Write-JsonAtomic -Path $StateFile -Value $state
        $pushResult = Send-BarsToRemote -Bars @($result.bars) -StatusSymbols $bridgeSymbols -LastBarTs ([string]$result.last_bar_ts)
        $status = "ok"
        $message = "rakuten rss prices sampled"
        if ($result.seen_prices -eq 0) {
            $status = "empty"
            $message = "rss returned no prices"
        } elseif ($result.bars_written -eq 0) {
            $status = "idle"
            $message = "rss sampled; current bar updated or unchanged"
        }
        if ([string]::IsNullOrWhiteSpace($PushUrl) -eq $false -and -not $pushResult.ok) {
            $status = "error"
            $message = "rss sampled but remote push failed"
        }
        Write-FeedStatus -Status $status -Message $message -StatusSymbols $bridgeSymbols -BarsWritten ([int]$result.bars_written) -LastBarTs ([string]$result.last_bar_ts) -Extra @{ remote_push = $pushResult }
        if ($result.seen_prices -gt 0) {
            Invoke-RunnerOnce
        }
        Write-Host ("{0} status={1} prices={2} bars_written={3} remote_ok={4} last_bar={5}" -f (ConvertTo-LocalIso -Value ([datetime]::Now)), $status, $result.seen_prices, $result.bars_written, $pushResult.ok, $result.last_bar_ts)
        if (-not $Loop) {
            break
        }
        Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
    }
} catch {
    $symbolsForStatus = @()
    try {
        $symbolsForStatus = Get-BridgeSymbols
    } catch {
        $symbolsForStatus = @()
    }
    Write-FeedStatus -Status "error" -Message "rakuten rss bridge failed" -StatusSymbols $symbolsForStatus -ErrorText "$($_.Exception.Message)"
    throw
} finally {
    if ($workbook -and (-not $KeepWorkbook) -and (-not $Visible)) {
        $workbook.Close($false) | Out-Null
    }
    if ($rssAddinWorkbook -and (-not $KeepWorkbook) -and (-not $Visible)) {
        $rssAddinWorkbook.Close($false) | Out-Null
    }
    if ($excel -and (-not $KeepWorkbook) -and (-not $Visible)) {
        $excel.Quit() | Out-Null
    }
}
