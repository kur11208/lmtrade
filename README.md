# LMTrade

Local paper-trading tools for Japanese ETF/day-trade monitoring.

The current setup is intentionally market-data-only for external broker/RSS integration:

- Rakuten MarketSpeed II RSS bridge reads market data through Excel.
- The PC bridge pushes sampled bars to the phone daytrade server.
- The phone runs the HTTP dashboards, watchdogs, and paper runner.
- Live order routing is disabled by configuration (`allow_live_order=false`).

## Main Components

- `etf_mobile/daytrade_app.py` - phone daytrade dashboard and feed endpoint.
- `etf_mobile/daytrade_runner.py` - paper-trading runner.
- `etf_mobile/rakuten_rss_bridge.ps1` - Windows MarketSpeed II RSS bridge.
- `etf_mobile/market_feed_start.ps1` - starts MarketSpeed II and the RSS bridge.
- `etf_mobile/market_feed_stop.ps1` - stops the market feed and optionally shuts down.
- `etf_mobile/phone_wake_pc.sh` - Termux Wake-on-LAN helper for scheduled PC startup.
- `etf_mobile/DAYTRADE_README.md` - detailed operational notes.

## Safety

This repository excludes local secrets, SSH keys, logs, Excel workbooks, SQLite files,
paper trade history, intraday market data, generated deployment archives, and runtime
queues. Review `.gitignore` before making a public repository.

## Basic Checks

```powershell
cd C:\work\lmtrade\etf_mobile
python -m py_compile daytrade_app.py daytrade_runner.py
python -m unittest
```
