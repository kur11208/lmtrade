from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import daytrade_market_calendar as market_calendar

DATA_DIR = Path("data_daytrade")
FEED_STATUS_FILE = DATA_DIR / "feed_status.json"
RUNNER_STATUS_FILE = DATA_DIR / "runner_status.json"


def set_data_dir(path: Path):
    global DATA_DIR, FEED_STATUS_FILE, RUNNER_STATUS_FILE
    DATA_DIR = Path(path)
    FEED_STATUS_FILE = DATA_DIR / "feed_status.json"
    RUNNER_STATUS_FILE = DATA_DIR / "runner_status.json"


def utc_now_text() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def local_now_text() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json_atomic(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_feed_status(
    adapter: str,
    status: str,
    message: str = "",
    symbols: list[str] | None = None,
    bars_written: int = 0,
    last_bar_ts: str = "",
    error: str = "",
):
    write_json_atomic(FEED_STATUS_FILE, {
        "kind": "feed",
        "adapter": adapter,
        "status": status,
        "message": message,
        "symbols": symbols or [],
        "bars_written": bars_written,
        "last_bar_ts": last_bar_ts,
        "last_feed_at": local_now_text(),
        "last_feed_at_utc": utc_now_text(),
        "last_error": error,
    })


def write_runner_status(
    status: str,
    message: str = "",
    symbols: list[str] | None = None,
    processed: int = 0,
    error: str = "",
):
    write_json_atomic(RUNNER_STATUS_FILE, {
        "kind": "runner",
        "status": status,
        "message": message,
        "symbols": symbols or [],
        "processed": processed,
        "last_runner_loop_at": local_now_text(),
        "last_runner_loop_at_utc": utc_now_text(),
        "last_error": error,
    })


def read_feed_status() -> dict:
    return read_json(FEED_STATUS_FILE, {})


def read_runner_status() -> dict:
    return read_json(RUNNER_STATUS_FILE, {})


def parse_local_ts(value: str) -> dt.datetime | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt)
        except Exception:
            pass
    return None


def age_seconds(value: str, now: dt.datetime | None = None) -> int | None:
    ts = parse_local_ts(value)
    if ts is None:
        return None
    now = now or dt.datetime.now()
    return int((now - ts).total_seconds())


def status_age_label(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}秒前"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分前"
    return f"{minutes // 60}時間{minutes % 60}分前"


def feed_health(feed_status: dict, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now()
    if not market_calendar.is_market_time(now):
        return {
            "label": "市場外",
            "tone": "",
            "age_sec": age_seconds(feed_status.get("last_feed_at", ""), now),
            "source_status": feed_status.get("status", ""),
        }

    age = age_seconds(feed_status.get("last_feed_at", ""), now)
    if not feed_status:
        return {"label": "未起動", "tone": "bad", "age_sec": None, "source_status": ""}
    if str(feed_status.get("status", "")).lower() in ("error", "failed"):
        return {"label": "エラー", "tone": "bad", "age_sec": age, "source_status": feed_status.get("status", "")}
    if age is None or age > 90:
        return {"label": "停止", "tone": "bad", "age_sec": age, "source_status": feed_status.get("status", "")}
    if age > 30:
        return {"label": "遅延", "tone": "warn", "age_sec": age, "source_status": feed_status.get("status", "")}
    return {"label": "稼働中", "tone": "ok", "age_sec": age, "source_status": feed_status.get("status", "")}


def build_http_status(symbol_states: list[dict], runtime_config: dict, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now()
    feed = read_feed_status()
    runner = read_runner_status()
    feed_health_info = feed_health(feed, now)
    return {
        "ok": True,
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "market": {
            "label": market_calendar.market_label(now),
            "is_open": market_calendar.is_market_time(now),
            "is_trading_day": market_calendar.is_trading_day(now.date()),
            "next_open": market_calendar.next_market_open(now).strftime("%Y-%m-%d %H:%M:%S"),
        },
        "runtime": {
            "trading_mode": runtime_config.get("trading_mode", "paper"),
            "market_data_adapter": runtime_config.get("market_data_adapter", "csv"),
            "allow_live_order": False,
            "api_usage": "market_data_only",
        },
        "feed": {
            **feed,
            "health_label": feed_health_info["label"],
            "health_tone": feed_health_info["tone"],
            "age_sec": feed_health_info["age_sec"],
        },
        "runner": runner,
        "symbols": symbol_states,
    }
