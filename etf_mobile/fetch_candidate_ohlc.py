from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import runner_pc_multi as runner
from backtest_real_ohlc import CANDIDATE_UNIVERSE_FILE, FALLBACK_SYMBOLS, REAL_DATA_DIR, SYMBOLS_FILE, read_json


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_candidates() -> list[dict]:
    rows = read_json(CANDIDATE_UNIVERSE_FILE, [])
    if not rows:
        rows = [{"symbol": symbol, "name": "", "asset_class": ""} for symbol in FALLBACK_SYMBOLS]
    out = []
    seen = set()
    for row in rows:
        symbol = str(row.get("symbol", "")).strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append({
            "symbol": symbol,
            "name": row.get("name", ""),
            "asset_class": row.get("asset_class", ""),
        })
    return out


def yahoo_ticker(symbol: str) -> str:
    return f"{symbol}.T"


def fetch_chart(symbol: str, start: dt.date) -> dict:
    period1 = int(dt.datetime.combine(start, dt.time.min, tzinfo=dt.timezone.utc).timestamp())
    period2 = int(time.time())
    params = urllib.parse.urlencode({
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "history",
    })
    url = f"{YAHOO_CHART_URL.format(ticker=urllib.parse.quote(yahoo_ticker(symbol)))}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def rows_from_chart(payload: dict) -> list[dict]:
    result = payload.get("chart", {}).get("result")
    if not result:
        return []
    result = result[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []

    rows = []
    for idx, ts in enumerate(timestamps):
        try:
            open_price = quote.get("open", [])[idx]
            high = quote.get("high", [])[idx]
            low = quote.get("low", [])[idx]
            close = quote.get("close", [])[idx]
            volume = quote.get("volume", [])[idx]
        except Exception:
            continue
        if None in (open_price, high, low, close):
            continue
        if close <= 0:
            continue

        adj_close = adj[idx] if idx < len(adj) and adj[idx] is not None else close
        factor = adj_close / close if close > 0 else 1.0
        date_s = dt.datetime.fromtimestamp(int(ts), tz=dt.timezone.utc).strftime("%Y-%m-%d")
        rows.append({
            "date": date_s,
            "open": round(float(open_price) * factor, 6),
            "high": round(float(high) * factor, 6),
            "low": round(float(low) * factor, 6),
            "close": round(float(adj_close), 6),
            "volume": int(volume or 0),
        })

    uniq = {row["date"]: row for row in rows}
    return [uniq[key] for key in sorted(uniq)]


def write_ohlc(symbol: str, rows: list[dict]):
    sym_dir = REAL_DATA_DIR / symbol
    sym_dir.mkdir(parents=True, exist_ok=True)
    path = sym_dir / "ohlc.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)


def ensure_support_files(item: dict, rows: list[dict]):
    symbol = item["symbol"]
    sym_dir = REAL_DATA_DIR / symbol
    latest = rows[-1]

    settings_path = sym_dir / "paper_settings.json"
    if not settings_path.exists():
        write_json(settings_path, {
            "symbol": symbol,
            "strategy_enabled": False,
            "buy_th": 0.5,
            "sell_th": 0.35,
            "risk_cash": 0.2,
            "atr_mult": 3.0,
            "fee_rate": runner.DEFAULT_FEE_RATE,
            "slippage_rate": runner.DEFAULT_SLIPPAGE_RATE,
            "trend_ma_days": runner.DEFAULT_TREND_MA_DAYS,
            "momentum_days": runner.DEFAULT_MOMENTUM_DAYS,
            "max_atr_rate": runner.DEFAULT_MAX_ATR_RATE,
            "asset_class": item.get("asset_class", ""),
        })

    state_path = sym_dir / "paper_state_atr.json"
    if not state_path.exists():
        write_json(state_path, {
            "symbol": symbol,
            "latest_date": latest["date"],
            "today_jst": latest["date"],
            "today_is_trading_day": True,
            "equity": 1.0,
            "base_equity": 100.0,
            "sim_amount_yen": 10000.0,
            "sim_pnl_rate": 0.0,
            "sim_pnl_yen": 0.0,
            "pred_prob": 0.0,
            "pred_source": "not_evaluated",
            "price_source": "ohlc.csv",
            "close": latest["close"],
            "open": latest["open"],
            "high": latest["high"],
            "low": latest["low"],
            "atr_14": max(latest["close"] * 0.02, 1.0),
            "current_w_today": 0.0,
            "current_position_today": "CASH",
            "position": "CASH",
            "pending_action": "HOLD_CASH",
            "next_w": 0.0,
            "next_position": "CASH",
            "display_market_status": "初期化済み",
            "display_today_status": "今日は現金待機",
            "display_next_plan": "現金維持",
            "display_note": "OHLC取得後の初期状態です。最適化後にrunnerで更新してください",
        })

    runner_state_path = sym_dir / "mobile_runner_state.json"
    if not runner_state_path.exists():
        write_json(runner_state_path, {
            "last_signal_run_date": "",
            "last_execution_run_date": "",
        })

    live_config_path = sym_dir / "paper_live_config.json"
    if not live_config_path.exists():
        write_json(live_config_path, {
            "symbol": symbol,
            "asset_class": item.get("asset_class", ""),
        })


def update_symbols_json(candidates: list[dict]):
    enabled_rows = []
    for item in candidates:
        symbol = item["symbol"]
        if (REAL_DATA_DIR / symbol / "ohlc.csv").exists():
            enabled_rows.append({
                "symbol": symbol,
                "name": item.get("name", ""),
                "asset_class": item.get("asset_class", ""),
                "enabled": True,
            })
    write_json(SYMBOLS_FILE, enabled_rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="overwrite existing ohlc.csv files too")
    parser.add_argument("--update-symbols", action="store_true", help="write fetched candidates to data/symbols.json")
    parser.add_argument("--start", default="2000-01-01")
    args = parser.parse_args()

    start = dt.datetime.strptime(args.start, "%Y-%m-%d").date()
    candidates = load_candidates()
    fetched = []
    skipped = []
    failed = []

    for item in candidates:
        symbol = item["symbol"]
        ohlc_path = REAL_DATA_DIR / symbol / "ohlc.csv"
        if ohlc_path.exists() and not args.refresh:
            skipped.append(symbol)
            continue
        try:
            payload = fetch_chart(symbol, start)
            rows = rows_from_chart(payload)
            if len(rows) < 200:
                failed.append({"symbol": symbol, "reason": f"too few rows: {len(rows)}"})
                continue
            write_ohlc(symbol, rows)
            ensure_support_files(item, rows)
            fetched.append({"symbol": symbol, "rows": len(rows), "first": rows[0]["date"], "last": rows[-1]["date"]})
            time.sleep(0.2)
        except Exception as e:
            failed.append({"symbol": symbol, "reason": f"{type(e).__name__}: {e}"})

    if args.update_symbols:
        update_symbols_json(candidates)

    print("fetched:")
    for item in fetched:
        print(f"- {item['symbol']}: {item['rows']} rows {item['first']}..{item['last']}")
    print("skipped_existing:")
    for symbol in skipped:
        print(f"- {symbol}")
    print("failed:")
    for item in failed:
        print(f"- {item['symbol']}: {item['reason']}")


if __name__ == "__main__":
    main()
