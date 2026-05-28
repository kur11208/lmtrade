# Daytrade paper tools

API/order routing is intentionally out of scope for now. These scripts cover paper trading, screening, replay/backtest, demo data feeding, and local storage.

Live orders are disabled by design. Future APIs are for market data only until a separate live-order build is explicitly requested.

## Basic run

```powershell
cd C:\work\lmtrade\etf_mobile
python daytrade_runner.py --once --replay-all
python daytrade_screener.py --top 10
python daytrade_backtest.py
python daytrade_app.py
```

Open `http://127.0.0.1:8010/`.

If port 8010 is busy:

```powershell
$env:DAYTRADE_PORT='8011'
python daytrade_app.py
```

## Phone resident mode

The phone supervisor starts both the existing ETF watchdog and the daytrade watchdog.

```sh
cd ~/etf_mobile
nohup sh watchdog_supervisor.sh >> logs/supervisor_launcher.log 2>&1 &
```

Daytrade runs on port `8010`:

```sh
curl -I http://127.0.0.1:8010/
tail -n 50 logs/watchdog_daytrade.log
tail -n 20 logs/daytrade_app.log
tail -n 20 logs/daytrade_runner.log
tail -n 20 logs/daytrade_screener.log
```

From the PC, deploy/restart after setting the phone SSH environment:

```powershell
.\deploy_to_phone.ps1 -Restart
```

If SSH is not set up, install the phone puller once and let it keep watching the PC update server:

```sh
curl -fsL -o ~/phone_auto_update.sh http://192.168.0.24:8765/phone_auto_update.sh
nohup sh ~/phone_auto_update.sh http://192.168.0.24:8765 30 >> ~/phone_auto_update_launcher.log 2>&1 &
```

After that, publish updates on the PC:

```powershell
cd C:\work\lmtrade\phone_deploy
.\publish_phone_update.ps1
```

## Paper runner

```powershell
python daytrade_runner.py
```

The runner reads `data_daytrade/<symbol>/intraday_bars.csv` and processes only new bars.

Current non-API controls:

- 1-minute bar settings
- 1-second runner polling by default
- exchange calendar handling for weekends, Japan holidays, and Tokyo exchange year-end/New-Year closures
- `/health` and `/status.json` HTTP status endpoints
- feed heartbeat in `data_daytrade/feed_status.json`
- runner heartbeat in `data_daytrade/runner_status.json`
- data freshness and stale-data display
- bar gap detection
- long setups: opening range breakout and VWAP reclaim
- short setups: opening range breakdown and VWAP rejection
- VWAP filter
- volume confirmation
- opening gap filter
- optional market filter using another local symbol
- risk-based position sizing
- max trades per day
- daily loss limit
- consecutive loss stop
- cooldown after exit
- slippage, spread, fee, tick-size simulation
- liquidity participation cap
- partial take profit
- trailing stop
- forced flat before close
- data gap warnings
- local alert CSV for ENTRY/EXIT/BLOCKED
- order candidate queue for paper/noop processing, including short-entry SELL and short-exit BUY candidates
- runtime config that forces `allow_live_order=false`

Set `allow_short=false` in a symbol's `paper_settings.json` to keep that symbol long-only. Set `allow_long=false` to test only the downside setups.

## Screener

```powershell
python daytrade_screener.py --top 10
```

Writes `data_daytrade/screener_results.json`.

To enable only the top N symbols in `symbols.json`:

```powershell
python daytrade_screener.py --top 4 --apply
```

The phone watchdog also starts `python daytrade_screener.py --loop`. By default it applies the top 4 symbols at `09:40` and `12:40` on trading days, keeps symbols with an open position enabled, and avoids re-selecting symbols stopped by the same-day loss/consecutive-loss guard. Configure this in `data_daytrade/runtime_config.json` with `screener_auto_apply`, `screener_top_n`, `screener_refresh_times`, and `screener_refresh_window_minutes`.

The score uses local intraday CSV only: turnover, intraday range, volume ratio, VWAP position, opening-range breakout distance, gap penalty, spread penalty, and data warnings.

## Backtest

```powershell
python daytrade_backtest.py
python daytrade_backtest.py --symbol 1570
```

Writes `logs/daytrade_backtest_results.json` with P/L, drawdown, trade count, win rate, and profit factor.

## Demo feed

Append pseudo intraday bars without any broker/data API:

```powershell
python daytrade_demo_feed.py --steps 3 --interval-minutes 1
python daytrade_runner.py --once
```

For one symbol:

```powershell
python daytrade_demo_feed.py --symbol 1570 --steps 5
```

Continuous demo feed:

```powershell
python daytrade_demo_feed.py --loop --realtime --run-runner --interval-minutes 1 --sleep 1
```

The phone watchdog runs the demo CSV feed automatically while `market_data_adapter` is `csv` or `demo_csv`. The demo feed appends bars for the whole screener universe, but only runs trading logic for currently enabled symbols. Set `DAYTRADE_DEMO_FEED=0` before starting `watchdog_supervisor.sh` to disable it.

## Rakuten MarketSpeed II RSS feed

This bridge is market-data only. It reads MarketSpeed II RSS values through
Excel and appends one-minute bars to `data_daytrade/<symbol>/intraday_bars.csv`.
It never sends live orders.

Prerequisites:

- MarketSpeed II is installed and logged in.
- Excel is installed on the same Windows PC.
- The MarketSpeed II RSS agreement/connection prompt has been completed in Excel.

Setup/probe with Excel visible:

```powershell
cd C:\work\lmtrade\etf_mobile
powershell -NoProfile -ExecutionPolicy Bypass -File .\rakuten_rss_bridge.ps1 -EnabledOnly -Visible -KeepWorkbook
```

If the workbook opens but prices stay blank, connect MarketSpeed II RSS from the
Excel ribbon or complete the RSS agreement prompt, then run the bridge again.

Run once and process the paper runner:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rakuten_rss_bridge.ps1 -EnabledOnly -RunRunner
```

Run continuously:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rakuten_rss_bridge.ps1 -EnabledOnly -Loop -RunRunner -PollSeconds 5
```

Push sampled bars to the phone daytrade server:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rakuten_rss_bridge.ps1 -EnabledOnly -Loop -PushUrl http://192.168.0.96:8010/feed/bars -PollSeconds 5
```

## SQLite export

CSV remains the live input format, but you can mirror bars/history into SQLite:

```powershell
python daytrade_sqlite.py
```

Creates `data_daytrade/daytrade.sqlite`.

## Alerts

The runner writes `alerts_daytrade.csv` under each symbol directory when a bar becomes ENTRY, EXIT, or BLOCKED.

```powershell
python daytrade_alerts.py --last 20
```

## Order candidates, no live orders

When ENTRY/EXIT appears, the runner appends a paper order candidate to:

```text
data_daytrade/order_queue.jsonl
```

List pending candidates:

```powershell
python daytrade_order_paper.py --list
```

This does not place broker orders. `data_daytrade/runtime_config.json` forces:

```json
{
  "api_usage": "market_data_only",
  "allow_live_order": false,
  "order_adapter": "paper"
}
```

## Future API feed

API integration should feed market data only:

```powershell
python daytrade_feed_api_stub.py
python daytrade_feed_api_stub.py --adapter api
```

The adapter layer is in `daytrade_market_data.py`. The current API adapter is a market-data-only skeleton that writes heartbeat status but does not place orders or fetch broker data until credentials/source code are added explicitly.

## CSV format

```csv
timestamp,open,high,low,close,volume
2026-05-20 09:00:00,31900,32100,31850,32050,120000
```

The timestamp is local market time. Duplicate timestamps are collapsed with the last row winning.

## Import external CSV

```powershell
python daytrade_import_csv.py --symbol 1570 --file C:\path\to\minute_bars.csv --run-runner
```

Accepted timestamp aliases include `timestamp`, `datetime`, `date_time`, `日時`, `時刻`. Price aliases include `open/high/low/close` and Japanese `始値/高値/安値/終値`.
