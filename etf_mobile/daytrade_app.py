from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
import csv
import html
import json
import os
import subprocess

import daytrade_market_calendar as market_calendar
import daytrade_status
import daytrade_view_model

DEVICE_PROP_KEYS = [
    "ro.product.manufacturer",
    "ro.product.brand",
    "ro.product.model",
    "ro.product.name",
    "ro.product.device",
    "ro.board.platform",
    "ro.hardware",
    "ro.product.cpu.abi",
    "ro.product.cpu.abilist",
    "ro.oem_unlock_supported",
    "ro.boot.flash.locked",
    "ro.boot.verifiedbootstate",
    "ro.build.version.release",
    "ro.build.version.sdk",
]

DATA_DIR = Path("data_daytrade")
SYMBOLS_FILE = DATA_DIR / "symbols.json"
SCREENER_FILE = DATA_DIR / "screener_results.json"
BACKTEST_FILE = Path("logs") / "daytrade_backtest_results.json"
RUNTIME_CONFIG_FILE = DATA_DIR / "runtime_config.json"
PC_WAKE_FILE = DATA_DIR / "pc_wake_state.json"

HOST = "0.0.0.0"
PORT = int(os.environ.get("DAYTRADE_PORT", "8010"))
CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)


def read_text_auto(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            pass
    return path.read_text(encoding="utf-8", errors="replace")


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(read_text_auto(path))
    except Exception:
        return default


def load_symbols():
    rows = load_json(SYMBOLS_FILE, [])
    return [row for row in rows if row.get("enabled", True) is not False]


def load_state(symbol: str):
    return load_json(DATA_DIR / symbol / "paper_state_daytrade.json", {
        "symbol": symbol,
        "strategy_reason": "state file missing",
        "current_position_today": "FLAT",
    })


def load_settings(symbol: str):
    settings = load_json(DATA_DIR / symbol / "paper_settings.json", {})
    return settings if isinstance(settings, dict) else {}


def parse_history_ts(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass
    return None


def parse_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def parse_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def normalize_incoming_bar(row):
    if not isinstance(row, dict):
        return None
    symbol = str(row.get("symbol", "")).strip()
    if not symbol or "/" in symbol or "\\" in symbol or symbol in (".", ".."):
        return None
    ts_obj = parse_history_ts(row.get("timestamp"))
    close = parse_float(row.get("close"))
    if ts_obj is None or close is None or close <= 0:
        return None
    open_price = parse_float(row.get("open"), close)
    high = parse_float(row.get("high"), max(open_price, close))
    low = parse_float(row.get("low"), min(open_price, close))
    volume = max(0.0, parse_float(row.get("volume"), 0.0))
    high = max(high, open_price, close)
    low = min(low, open_price, close)
    return {
        "symbol": symbol,
        "timestamp": ts_obj.strftime("%Y-%m-%d %H:%M:%S"),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def write_intraday_bar_from_feed(bar):
    symbol = bar["symbol"]
    path = DATA_DIR / symbol / "intraday_bars.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    rows_by_ts = {}
    if path.exists() and path.stat().st_size > 0:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    ts = str(row.get("timestamp", "")).strip()
                    if ts:
                        rows_by_ts[ts] = {
                            "timestamp": ts,
                            "open": parse_float(row.get("open"), bar["open"]),
                            "high": parse_float(row.get("high"), bar["high"]),
                            "low": parse_float(row.get("low"), bar["low"]),
                            "close": parse_float(row.get("close"), bar["close"]),
                            "volume": parse_float(row.get("volume"), 0.0),
                        }
        except Exception:
            rows_by_ts = {}

    existing = rows_by_ts.get(bar["timestamp"])
    appended = existing is None
    if existing:
        merged = {
            "timestamp": bar["timestamp"],
            "open": existing.get("open") if existing.get("open") is not None else bar["open"],
            "high": max(parse_float(existing.get("high"), bar["high"]), bar["high"]),
            "low": min(parse_float(existing.get("low"), bar["low"]), bar["low"]),
            "close": bar["close"],
            "volume": max(parse_float(existing.get("volume"), 0.0), bar["volume"]),
        }
    else:
        merged = {
            "timestamp": bar["timestamp"],
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
        }
    rows_by_ts[bar["timestamp"]] = merged

    def sort_key(item):
        parsed = parse_history_ts(item.get("timestamp"))
        return parsed or datetime.min

    rows = sorted(rows_by_ts.values(), key=sort_key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "timestamp": row["timestamp"],
                "open": round(parse_float(row.get("open"), 0.0), 6),
                "high": round(parse_float(row.get("high"), 0.0), 6),
                "low": round(parse_float(row.get("low"), 0.0), 6),
                "close": round(parse_float(row.get("close"), 0.0), 6),
                "volume": round(max(0.0, parse_float(row.get("volume"), 0.0)), 0),
            })
    return appended


def accept_feed_bars(payload):
    raw_bars = payload.get("bars", []) if isinstance(payload, dict) else []
    if not isinstance(raw_bars, list):
        return {"ok": False, "error": "bars must be a list", "accepted": 0}

    accepted = []
    appended = 0
    for raw in raw_bars[:200]:
        bar = normalize_incoming_bar(raw)
        if bar is None:
            continue
        if write_intraday_bar_from_feed(bar):
            appended += 1
        accepted.append(bar)

    symbols = sorted({bar["symbol"] for bar in accepted})
    last_bar_ts = max((bar["timestamp"] for bar in accepted), default="")
    daytrade_status.set_data_dir(DATA_DIR)
    daytrade_status.write_feed_status(
        adapter=str(payload.get("adapter", "remote_feed"))[:64] if isinstance(payload, dict) else "remote_feed",
        status="ok" if accepted else "empty",
        message="remote feed bars accepted" if accepted else "no valid bars in remote feed payload",
        symbols=symbols,
        bars_written=appended,
        last_bar_ts=last_bar_ts,
    )
    return {
        "ok": True,
        "accepted": len(accepted),
        "appended": appended,
        "symbols": symbols,
        "last_bar_ts": last_bar_ts,
    }


def load_history(symbol: str):
    path = DATA_DIR / symbol / "paper_history_daytrade.csv"
    if not path.exists():
        return []

    rows = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts_obj = parse_history_ts(row.get("timestamp"))
                equity = parse_float(row.get("equity"))
                price = parse_float(row.get("price"))
                if ts_obj is None or equity is None:
                    continue
                rows.append({
                    "timestamp": ts_obj,
                    "date": ts_obj.strftime("%H:%M"),
                    "value": equity - 1.0,
                    "equity": equity,
                    "price": price,
                    "vwap": parse_float(row.get("vwap")),
                    "or_high": parse_float(row.get("or_high")),
                    "or_low": parse_float(row.get("or_low")),
                    "stop_price": parse_float(row.get("stop_price")),
                    "target_price": parse_float(row.get("target_price")),
                    "action": str(row.get("action", "")).strip(),
                    "signal": str(row.get("signal", "")).strip(),
                })
    except Exception:
        return []

    rows.sort(key=lambda row: row["timestamp"])
    if rows:
        latest_day = rows[-1]["timestamp"].date()
        rows = [row for row in rows if row["timestamp"].date() == latest_day]
    return rows[-160:]


def load_screener_map():
    rows = load_json(SCREENER_FILE, [])
    return {str(row.get("symbol")): row for row in rows}


def load_backtest_map():
    rows = load_json(BACKTEST_FILE, [])
    return {str(row.get("symbol")): row for row in rows}


def load_runtime_config():
    cfg = load_json(RUNTIME_CONFIG_FILE, {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["allow_live_order"] = False
    return cfg


def format_pct(rate):
    try:
        return f"{float(rate) * 100:.2f}%"
    except Exception:
        return "-"


def format_number(value, decimals=2):
    try:
        x = float(value)
    except Exception:
        return "-"
    text = f"{x:,.{decimals}f}"
    return text.rstrip("0").rstrip(".")


def format_price(value):
    return format_number(value, decimals=1)


def format_score(value):
    return format_number(value, decimals=1)


def format_age(seconds):
    try:
        sec = int(float(seconds))
    except Exception:
        return "-"
    if sec < -5:
        return f"{abs(sec)}秒後"
    if sec < 60:
        return f"{sec}秒前"
    minutes = sec // 60
    if minutes < 60:
        return f"{minutes}分前"
    return f"{minutes // 60}時間{minutes % 60}分前"


def latest_ts_label(value):
    ts = parse_history_ts(value)
    if ts is None:
        return str(value or "-")
    return ts.strftime("%m/%d %H:%M")


def is_market_session(now: datetime):
    return market_calendar.is_market_time(now)


def current_freshness_state(st: dict, settings: dict, now: datetime) -> dict:
    display = dict(st)
    ts = parse_history_ts(st.get("latest_ts"))
    age_sec = None if ts is None else int((now - ts).total_seconds())
    warn_sec = max(
        1,
        parse_int(
            settings.get("stale_data_warn_seconds", st.get("stale_data_warn_seconds")),
            90,
        ),
    )
    block_sec = max(
        warn_sec,
        parse_int(
            settings.get("stale_data_block_seconds", st.get("stale_data_block_seconds")),
            300,
        ),
    )

    if age_sec is not None and not is_market_session(now):
        freshness = "market_closed"
    elif age_sec is None:
        freshness = "missing"
    elif age_sec < -5:
        freshness = "future"
    elif age_sec <= warn_sec:
        freshness = "fresh"
    elif age_sec <= block_sec:
        freshness = "stale"
    else:
        freshness = "blocked_stale"

    display["data_age_sec"] = age_sec
    display["data_freshness_status"] = freshness
    if freshness == "market_closed" and str(display.get("current_position_today", "FLAT")).strip() != "LONG":
        display["signal"] = "WAIT"
        display["last_action"] = "MARKET_CLOSED"
        display["strategy_reason"] = "market closed"
    return display


def row_html(label, value):
    return f'<div class="row"><span class="label">{html.escape(label)}</span><span class="value">{html.escape(str(value))}</span></div>'


def metric_html(label, value, tone=""):
    cls = f"metric {tone}".strip()
    return f'<div class="{cls}"><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></div>'


def jp_position(value):
    mapping = {
        "LONG": "保有中",
        "FLAT": "ノーポジ",
        "CASH": "ノーポジ",
    }
    text = str(value or "").strip()
    return mapping.get(text, text or "-")


def badge_text(signal):
    mapping = {
        "ENTRY": "買い",
        "EXIT": "手仕舞い",
        "HOLD": "保有",
        "WAIT": "待機",
        "BLOCKED": "見送り",
    }
    text = str(signal or "").strip()
    return mapping.get(text, text or "-")


def jp_action(value):
    mapping = {
        "BUY_BREAKOUT": "ブレイク買い",
        "BUY_VWAP_RECLAIM": "VWAP回復買い",
        "SELL_TARGET": "利確",
        "SELL_PARTIAL_TARGET": "一部利確",
        "SELL_STOP": "損切り",
        "SELL_EOD": "大引け前手仕舞い",
        "SELL_STALE_EOD": "データ停止後の手仕舞い",
        "SELL_DISABLED": "停止で手仕舞い",
        "HOLD_LONG": "保有継続",
        "WAIT": "待機",
        "NO_NEW_ENTRY": "新規停止",
        "DAILY_LOSS_STOP": "日次損失停止",
        "DISABLED": "戦略停止",
        "NEW_DAY": "新規営業日",
        "MARKET_CLOSED": "市場外",
    }
    text = str(value or "").strip()
    return mapping.get(text, text or "-")


def jp_reason(value):
    mapping = {
        "state file missing": "状態ファイル未作成",
        "opening range is still forming": "寄り付きレンジ形成中",
        "opening range breakout above vwap": "寄り付きレンジ上抜け、VWAP上",
        "vwap reclaim": "VWAP回復",
        "breakout or vwap condition not met": "ブレイクまたはVWAP条件が未達",
        "position open": "ポジション保有中",
        "exit by target": "利確条件到達",
        "exit by stop": "損切り条件到達",
        "exit by eod": "大引け前の強制手仕舞い",
        "exit by stale_eod": "データ停止後の手仕舞い",
        "exit by disabled": "戦略停止により手仕舞い",
        "partial target reached": "一部利確条件到達",
        "daily loss or consecutive loss limit reached": "日次損失または連敗上限に到達",
        "daily loss limit reached": "日次損失上限に到達",
        "consecutive loss limit reached": "連敗上限に到達",
        "new entry window closed": "新規エントリー時間終了",
        "strategy disabled": "戦略停止中",
        "new trading day": "新規営業日",
        "market closed": "市場外",
        "waiting": "待機中",
        "risk size is below minimum position": "リスク許容内の建玉が小さすぎる",
        "not enough intraday bars": "日中足不足",
        "volume confirmation not met": "出来高確認が未達",
        "volume baseline unavailable": "出来高基準が不足",
        "opening gap outside range": "寄り付きギャップが許容外",
        "market filter not met": "地合い条件が未達",
        "max trades per day reached": "本日の取引回数上限に到達",
        "cooldown active": "クールダウン中",
        "data quality warning": "データ品質警告",
        "notional below minimum": "最低売買金額未満",
        "stale data blocked": "価格データ遅延で停止",
    }
    text = str(value or "").strip()
    return mapping.get(text, text or "-")


def jp_market(value):
    mapping = {
        "market filter off": "地合い判定なし",
        "market filter skipped": "地合い判定スキップ",
        "market data unavailable": "地合いデータなし",
        "market return weak": "地合い弱い",
        "market under vwap": "地合いVWAP下",
        "market ok": "地合いOK",
    }
    text = str(value or "").strip()
    if "," in text:
        return " / ".join(jp_market(part) for part in text.split(","))
    return mapping.get(text, text or "-")


def jp_data_warning(value):
    text = str(value or "").strip()
    if not text:
        return "正常"
    if text.startswith("bar gap "):
        return "足間隔あり " + text.replace("bar gap ", "")
    return text


def jp_freshness(value):
    mapping = {
        "fresh": "正常",
        "stale": "遅延注意",
        "blocked_stale": "遅延停止",
        "market_closed": "市場外",
        "future": "未来足",
        "missing": "未取得",
    }
    text = str(value or "").strip()
    return mapping.get(text, text or "-")


def jp_screener_reasons(values):
    mapping = {
        "ok": "良好",
        "no intraday bars": "日中足なし",
        "opening range incomplete": "寄り付きレンジ未完成",
        "gap too large": "ギャップ過大",
        "spread penalty": "スプレッド減点",
        "no liquidity": "流動性不足",
    }
    out = []
    for value in values:
        text = str(value)
        if text.startswith("bar gap "):
            out.append("足間隔あり " + text.replace("bar gap ", ""))
        else:
            out.append(mapping.get(text, text))
    return "、".join(out) if out else "-"


def data_state_label(st: dict):
    freshness = str(st.get("data_freshness_status", "")).strip()
    if freshness == "market_closed":
        return "市場外"
    if freshness in ("blocked_stale", "missing"):
        return "データ停止"
    if freshness == "stale":
        return "遅延注意"
    if freshness == "fresh":
        return "監視中"
    if freshness == "future":
        return "未来足"
    return jp_freshness(freshness)


def feed_state_label(st: dict):
    return daytrade_view_model.feed_label_from_freshness(st.get("data_freshness_status", ""))[0]


def display_reason_text(st: dict):
    override = daytrade_view_model.display_reason(st.get("last_action", ""), st.get("strategy_reason", ""))
    if override:
        return override
    return jp_reason(st.get("strategy_reason", "-"))


def condition_text(st: dict, settings: dict, now: datetime):
    position = str(st.get("current_position_today", "FLAT")).strip()
    if position == "LONG":
        parts = []
        if st.get("stop_price") is not None:
            parts.append(f"損切り {format_price(st.get('stop_price'))}")
        if st.get("partial_target_price") is not None and not st.get("partial_exited"):
            parts.append(f"一部利確 {format_price(st.get('partial_target_price'))}")
        if st.get("target_price") is not None:
            parts.append(f"利確 {format_price(st.get('target_price'))}")
        parts.append(f"手仕舞い {settings.get('force_flat_time') or st.get('force_flat_time', '15:25')}")
        return " / ".join(parts)

    freshness = str(st.get("data_freshness_status", "")).strip()
    if freshness == "market_closed":
        if not market_calendar.is_trading_day(now.date()):
            return "休場日。次の取引時間まで新規判断なし"
        return "市場外。次の取引時間まで新規判断なし"
    if freshness in ("missing", "blocked_stale"):
        return "価格データ受信待ち"
    if st.get("no_new_entries"):
        return f"新規停止。再開は次の取引時間"

    reason = str(st.get("strategy_reason", "")).strip()
    if reason == "volume confirmation not met":
        current = format_number(st.get("volume_ratio"), 2)
        needed = format_number(settings.get("min_volume_ratio", 1.2), 2)
        return f"出来高倍率 {current} / 必要 {needed}"
    if reason == "market filter not met":
        return f"地合い待ち: {jp_market(st.get('market_filter_reason'))}"
    if reason == "opening range is still forming":
        return f"寄り付きレンジ形成中: {settings.get('opening_range_minutes', 30)}分"
    if reason == "cooldown active":
        until = latest_ts_label(st.get("cooldown_until_ts"))
        return f"クールダウン中: {until}まで"

    or_high = format_price(st.get("opening_range_high"))
    vwap = format_price(st.get("vwap"))
    vol = format_number(st.get("volume_ratio"), 2)
    needed = format_number(settings.get("min_volume_ratio", 1.2), 2)
    return f"OR高値 {or_high} 上抜け / VWAP {vwap} 回復 / 出来高 {vol}/{needed}"


def should_show_chart(st: dict, rows: list[dict], now: datetime):
    if len(rows) < 2:
        return False
    latest_ts = rows[-1].get("timestamp")
    if latest_ts is None:
        return False
    if not market_calendar.is_trading_day(now.date()):
        return False
    if str(st.get("data_freshness_status", "")) == "market_closed" and latest_ts.date() != now.date():
        return False
    return True


def elapsed_x_values(rows):
    timestamps = [row.get("timestamp") for row in rows]
    if timestamps and all(ts is not None for ts in timestamps):
        first_ts = timestamps[0]
        return [(ts - first_ts).total_seconds() / 60.0 for ts in timestamps]
    return [float(i) for i in range(len(rows))]


def build_price_svg(rows, st=None, width=760, height=230):
    if len(rows) < 2:
        return '<div class="chart-empty">表示できる日中履歴がまだありません。</div>'

    left = 58
    right = 24
    top = 18
    bottom = 32
    plot_w = width - left - right
    plot_h = height - top - bottom
    x_values = elapsed_x_values(rows)
    x_min = min(x_values)
    x_max = max(x_values)
    if abs(x_max - x_min) < 1e-9:
        x_values = [float(i) for i in range(len(rows))]
        x_min = min(x_values)
        x_max = max(x_values)

    prices = [row["price"] for row in rows if row.get("price") is not None]
    if not prices:
        return '<div class="chart-empty">価格履歴がまだありません。</div>'

    overlay_values = list(prices)
    for key in ("vwap", "or_high", "or_low", "stop_price", "target_price"):
        overlay_values.extend(row[key] for row in rows if row.get(key) is not None)
    if st:
        overlay_values.extend(
            parse_float(st.get(key))
            for key in ("vwap", "opening_range_high", "opening_range_low", "stop_price", "target_price", "partial_target_price")
            if parse_float(st.get(key)) is not None
        )

    v_min = min(overlay_values)
    v_max = max(overlay_values)
    if abs(v_max - v_min) < 1e-9:
        pad = max(abs(v_max) * 0.002, 1.0)
    else:
        pad = (v_max - v_min) * 0.12
    v_min -= pad
    v_max += pad

    def x_pos_by_value(value):
        return left + plot_w * (value - x_min) / max(1e-9, x_max - x_min)

    def x_pos(i):
        return x_pos_by_value(x_values[i])

    def y_pos(value):
        return top + (v_max - value) / max(1e-9, v_max - v_min) * plot_h

    grid = []
    labels = []
    for idx in range(4):
        frac = idx / 3
        y = top + plot_h * frac
        value = v_max - (v_max - v_min) * frac
        grid.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e5e7eb" />')
        labels.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#64748b">{html.escape(format_price(value))}</text>')

    price_points = " ".join(
        f"{x_pos(i):.1f},{y_pos(row['price']):.1f}"
        for i, row in enumerate(rows)
        if row.get("price") is not None
    )

    def line_for_series(key, color, dash=""):
        points = [
            f"{x_pos(i):.1f},{y_pos(row[key]):.1f}"
            for i, row in enumerate(rows)
            if row.get(key) is not None
        ]
        if len(points) < 2:
            return ""
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        return f'<polyline fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"{dash_attr} points="{" ".join(points)}" />'

    def level_line(value, color, label, dash="6 4"):
        if value is None:
            return ""
        y = y_pos(value)
        return (
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{color}" stroke-width="1.8" stroke-dasharray="{dash}" />'
            f'<text x="{left + plot_w - 4}" y="{y - 4:.1f}" text-anchor="end" font-size="10" fill="{color}">{html.escape(label)}</text>'
        )

    action_marks = []
    for i, row in enumerate(rows):
        if row.get("price") is None:
            continue
        action = str(row.get("action", ""))
        if action.startswith("BUY"):
            color = "#0f766e"
            label = "買"
        elif action.startswith("SELL"):
            color = "#b91c1c"
            label = "売"
        else:
            continue
        x = x_pos(i)
        y = y_pos(row["price"])
        action_marks.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{color}" />')
        action_marks.append(f'<text x="{x:.1f}" y="{y - 9:.1f}" text-anchor="middle" font-size="10" fill="{color}">{label}</text>')

    label_indexes = []
    for target in [x_min, (x_min + x_max) / 2, x_max]:
        idx = min(range(len(x_values)), key=lambda i: abs(x_values[i] - target))
        if idx not in label_indexes:
            label_indexes.append(idx)
    x_labels = []
    for idx in sorted(label_indexes):
        anchor = "middle"
        if idx == 0:
            anchor = "start"
        elif idx == len(rows) - 1:
            anchor = "end"
        label = rows[idx]["date"]
        if idx == 0:
            label = rows[idx]["timestamp"].strftime("%m/%d %H:%M")
        x_labels.append(f'<text x="{x_pos(idx):.1f}" y="{height - 10}" text-anchor="{anchor}" font-size="11" fill="#64748b">{html.escape(label)}</text>')

    st = st or {}
    last = prices[-1]
    return f"""
    <svg class="price-svg" viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" aria-label="価格チャート">
        <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" rx="8" />
        {''.join(grid)}
        {''.join(labels)}
        <polyline fill="none" stroke="#2563eb" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="{price_points}" />
        {line_for_series("vwap", "#7c3aed", "5 4")}
        {level_line(parse_float(st.get("opening_range_high")), "#334155", "OR高")}
        {level_line(parse_float(st.get("opening_range_low")), "#64748b", "OR安")}
        {level_line(parse_float(st.get("stop_price")), "#b91c1c", "損切")}
        {level_line(parse_float(st.get("partial_target_price")), "#c2410c", "一部")}
        {level_line(parse_float(st.get("target_price")), "#15803d", "利確")}
        {''.join(action_marks)}
        <circle cx="{x_pos(len(rows) - 1):.1f}" cy="{y_pos(last):.1f}" r="4.5" fill="#2563eb" />
        {''.join(x_labels)}
    </svg>
    <div class="legend"><span class="blue">価格</span><span class="purple">VWAP</span><span>OR高/安</span><span class="red">損切</span><span class="green">利確</span></div>
    """


def build_asset_svg(rows, width=760, height=210):
    return build_price_svg(rows, width=width, height=height)


def build_status_summary(items, runtime_config, now: datetime):
    daytrade_status.set_data_dir(DATA_DIR)
    states = []
    for item in items:
        symbol = str(item.get("symbol", ""))
        states.append(current_freshness_state(load_state(symbol), load_settings(symbol), now))
    long_count = sum(1 for st in states if st.get("current_position_today") == "LONG")
    stopped_count = sum(1 for st in states if str(st.get("data_freshness_status", "")) in ("blocked_stale", "missing", "future"))
    stale_count = sum(1 for st in states if str(st.get("data_freshness_status", "")) == "stale")
    market_label = market_calendar.market_label(now)
    live_order = "OFF" if runtime_config.get("allow_live_order") is False else "要確認"
    latest_runner = max((str(st.get("last_runner_at", "")) for st in states), default="")
    latest_data = max((str(st.get("latest_ts", "")) for st in states), default="")
    data_label, tone = daytrade_view_model.aggregate_data_label(stopped_count, stale_count, is_market_session(now))
    feed_health = daytrade_status.feed_health(daytrade_status.read_feed_status(), now)
    feed_label = feed_health["label"]
    feed_tone = feed_health["tone"]

    return f"""
    <div class="status-bar">
        {metric_html("市場", market_label)}
        {metric_html("監視", f"{len(items)}銘柄", "ok")}
        {metric_html("建玉", f"{long_count}件", "warn" if long_count else "")}
        {metric_html("データ", data_label, tone)}
        {metric_html("feed", feed_label, feed_tone)}
        {metric_html("実売買", live_order, "safe")}
        {metric_html("最終足", latest_ts_label(latest_data))}
        {metric_html("runner", latest_runner or "-")}
    </div>
    """


def chart_block(st, hist, now):
    if not should_show_chart(st, hist, now):
        return ""
    return f"""
    <div class="chart-title">価格チャート</div>
    {build_price_svg(hist, st)}
    """


def render_card(item: dict, screener: dict, backtests: dict, runtime_config: dict, now: datetime):
    symbol = str(item.get("symbol", ""))
    name = str(item.get("name", ""))
    st = load_state(symbol)
    settings = load_settings(symbol)
    st = current_freshness_state(st, settings, now)
    hist = load_history(symbol)
    screen = screener.get(symbol, {})
    bt = backtests.get(symbol, {})
    signal = str(st.get("signal", "WAIT"))
    badge_class = "badge " + signal.lower()

    closed = int(float(st.get("closed_trade_count") or 0))
    wins = int(float(st.get("win_count") or 0))
    win_rate = "-" if closed <= 0 else f"{wins / closed * 100:.1f}%"
    pf = st.get("profit_factor")
    pf_text = "-" if pf is None else format_number(pf, 2)
    screener_score = screen.get("score", item.get("screener_score", "-"))
    live_order = "OFF" if runtime_config.get("allow_live_order") is False else "要確認"
    freshness = f"{jp_freshness(st.get('data_freshness_status'))} / {format_age(st.get('data_age_sec'))}"
    next_condition = condition_text(st, settings, now)

    primary_rows = [
        row_html("状態", data_state_label(st)),
        row_html("建玉", jp_position(st.get("current_position_today", "FLAT"))),
        row_html("判定", jp_action(st.get("last_action", "-"))),
        row_html("理由", display_reason_text(st)),
        row_html("次の条件", next_condition),
        row_html("最新足", latest_ts_label(st.get("latest_ts"))),
        row_html("feed", feed_state_label(st)),
        row_html("runner", st.get("last_runner_at", "-")),
        row_html("実売買", live_order),
    ]
    detail_rows = [
        row_html("累計損益", format_pct(st.get("sim_pnl_rate"))),
        row_html("本日損益", format_pct(st.get("daily_pnl_rate"))),
        row_html("銘柄スコア", format_score(screener_score)),
        row_html("価格", format_price(st.get("last_price"))),
        row_html("VWAP", format_price(st.get("vwap"))),
        row_html("OR高値", format_price(st.get("opening_range_high"))),
        row_html("OR安値", format_price(st.get("opening_range_low"))),
        row_html("出来高倍率", format_number(st.get("volume_ratio"), 2)),
        row_html("ギャップ", format_pct(st.get("gap_rate"))),
        row_html("地合い", jp_market(st.get("market_filter_reason", "-"))),
        row_html("データ品質", jp_data_warning(st.get("data_warning"))),
        row_html("鮮度", freshness),
        row_html("想定足", f"{st.get('bar_interval_minutes', '-')}分足"),
        row_html("建玉比率", format_pct(st.get("current_w_today"))),
        row_html("入口", format_price(st.get("entry_price"))),
        row_html("損切り", format_price(st.get("stop_price"))),
        row_html("一部利確", format_price(st.get("partial_target_price"))),
        row_html("利確", format_price(st.get("target_price"))),
        row_html("戦績", f"{closed}件 / 勝率 {win_rate} / PF {pf_text}"),
        row_html("新規停止", settings.get("no_new_entry_time") or st.get("no_new_entry_time", "-")),
        row_html("強制手仕舞い", settings.get("force_flat_time") or st.get("force_flat_time", "-")),
        row_html("API用途", "情報取得のみ"),
        row_html("注文候補", st.get("last_order_candidate_side", "-")),
    ]
    if bt:
        detail_rows.extend([
            row_html("検証損益", format_pct(bt.get("pnl_rate"))),
            row_html("検証DD", format_pct(bt.get("max_drawdown"))),
            row_html("検証取引", bt.get("closed_trade_count", "-")),
        ])

    reasons = screen.get("reasons", [])
    reason_text = jp_screener_reasons(reasons)

    return f"""
    <div class="card">
        <div class="card-head">
            <div>
                <div class="symbol">{html.escape(symbol)}</div>
                <div class="name">{html.escape(name)}</div>
            </div>
            <div class="{badge_class}">{html.escape(badge_text(signal))}</div>
        </div>
        <div class="rows">{''.join(primary_rows)}</div>
        {chart_block(st, hist, now)}
        <details class="details">
            <summary>詳細</summary>
            {''.join(detail_rows)}
            <div class="note">スクリーナー: {html.escape(reason_text)}</div>
        </details>
    </div>
    """


def render_ranking(screener: dict):
    rows = sorted(screener.values(), key=lambda row: float(row.get("score") or 0), reverse=True)[:8]
    if not rows:
        return ""
    chips = []
    for row in rows:
        chips.append(
            f'<div class="chip"><span>{html.escape(str(row.get("symbol", "")))}</span><strong>{html.escape(format_score(row.get("score")))}</strong></div>'
        )
    return f'<div class="rank-strip">{"".join(chips)}</div>'


def build_page():
    daytrade_status.set_data_dir(DATA_DIR)
    screener = load_screener_map()
    backtests = load_backtest_map()
    runtime_config = load_runtime_config()
    items = load_symbols()
    now = datetime.now()
    cards = [render_card(item, screener, backtests, runtime_config, now) for item in items]
    updated = now.strftime("%Y-%m-%d %H:%M:%S")
    ranking = render_ranking(screener)
    summary = build_status_summary(items, runtime_config, now)
    return f"""
    <!doctype html>
    <html lang="ja">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>デイトレ ペーパートレード</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background: #f6f7f9;
                color: #172033;
            }}
            .page {{
                max-width: 1500px;
                margin: 0 auto;
                padding: 18px;
            }}
            .title-row {{
                display: flex;
                justify-content: space-between;
                align-items: baseline;
                gap: 14px;
                margin-bottom: 12px;
            }}
            .title {{
                font-size: 26px;
                font-weight: 750;
                line-height: 1.2;
            }}
            .updated {{
                font-size: 13px;
                color: #64748b;
                white-space: nowrap;
            }}
            .status-bar {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                gap: 8px;
                margin-bottom: 12px;
            }}
            .metric {{
                border: 1px solid #d9e0e8;
                border-radius: 8px;
                background: #ffffff;
                padding: 9px 10px;
                min-width: 0;
            }}
            .metric span {{
                display: block;
                color: #64748b;
                font-size: 12px;
                line-height: 1.2;
            }}
            .metric strong {{
                display: block;
                margin-top: 3px;
                font-size: 15px;
                line-height: 1.25;
                overflow-wrap: anywhere;
            }}
            .metric.ok {{ border-color: #bbf7d0; background: #f0fdf4; }}
            .metric.warn {{ border-color: #fde68a; background: #fffbeb; }}
            .metric.bad {{ border-color: #fecaca; background: #fef2f2; }}
            .metric.safe {{ border-color: #bae6fd; background: #f0f9ff; }}
            .rank-strip {{
                display: flex;
                gap: 8px;
                overflow-x: auto;
                padding: 0 0 14px 0;
            }}
            .chip {{
                min-width: 94px;
                border: 1px solid #d9e0e8;
                border-radius: 8px;
                padding: 7px 9px;
                background: #ffffff;
                display: flex;
                justify-content: space-between;
                gap: 10px;
                font-size: 13px;
            }}
            .grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(min(100%, 430px), 1fr));
                gap: 16px;
                align-items: start;
            }}
            .card {{
                background: #ffffff;
                border: 1px solid #d9e0e8;
                border-radius: 8px;
                padding: 16px;
                box-shadow: 0 2px 10px rgba(15, 23, 42, 0.04);
            }}
            .card-head {{
                display: flex;
                justify-content: space-between;
                gap: 12px;
                align-items: start;
                margin-bottom: 12px;
            }}
            .symbol {{
                font-size: 22px;
                font-weight: 750;
            }}
            .name {{
                margin-top: 2px;
                color: #64748b;
                font-size: 13px;
                line-height: 1.4;
            }}
            .badge {{
                border: 1px solid #cbd5e1;
                border-radius: 999px;
                color: #334155;
                background: #f8fafc;
                font-weight: 700;
                font-size: 12px;
                padding: 5px 9px;
                white-space: nowrap;
            }}
            .badge.entry {{ color: #075985; background: #e0f2fe; border-color: #bae6fd; }}
            .badge.exit {{ color: #7f1d1d; background: #fee2e2; border-color: #fecaca; }}
            .badge.hold {{ color: #14532d; background: #dcfce7; border-color: #bbf7d0; }}
            .badge.blocked {{ color: #713f12; background: #fef3c7; border-color: #fde68a; }}
            .rows {{
                display: grid;
                gap: 5px;
            }}
            .row {{
                display: grid;
                grid-template-columns: 96px minmax(0, 1fr);
                gap: 10px;
                line-height: 1.45;
                font-size: 14px;
            }}
            .label {{
                color: #64748b;
            }}
            .value {{
                min-width: 0;
                overflow-wrap: anywhere;
            }}
            .chart-title {{
                margin-top: 14px;
                margin-bottom: 6px;
                font-weight: 750;
                font-size: 14px;
            }}
            .price-svg {{
                width: 100%;
                height: 230px;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                background: #fff;
                display: block;
            }}
            .legend {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                color: #64748b;
                font-size: 12px;
                margin-top: 6px;
            }}
            .legend .blue {{ color: #2563eb; }}
            .legend .purple {{ color: #7c3aed; }}
            .legend .red {{ color: #b91c1c; }}
            .legend .green {{ color: #15803d; }}
            .chart-empty {{
                height: 210px;
                border: 1px dashed #cbd5e1;
                border-radius: 8px;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 14px;
                text-align: center;
                color: #64748b;
                background: #fbfcfd;
            }}
            .details {{
                margin-top: 10px;
                border-top: 1px solid #eef2f7;
                padding-top: 8px;
            }}
            .details summary {{
                cursor: pointer;
                font-weight: 750;
                color: #334155;
            }}
            .details .row {{
                margin-top: 4px;
                font-size: 13px;
                grid-template-columns: 100px minmax(0, 1fr);
            }}
            .note {{
                margin-top: 8px;
                color: #475569;
                font-size: 13px;
                line-height: 1.5;
            }}
            @media (max-width: 680px) {{
                .page {{ padding: 12px; }}
                .title-row {{ display: block; }}
                .updated {{ margin-top: 6px; }}
                .row {{ grid-template-columns: 86px minmax(0, 1fr); }}
                .price-svg, .chart-empty {{ height: 190px; }}
            }}
        </style>
        <script>
            setTimeout(() => location.reload(), 2000);
        </script>
    </head>
    <body>
        <div class="page">
            <div class="title-row">
                <div class="title">デイトレ ペーパートレード</div>
                <div class="updated">更新 {html.escape(updated)}</div>
            </div>
            {summary}
            {ranking}
            <div class="grid">{''.join(cards)}</div>
        </div>
    </body>
    </html>
    """


def build_status_payload():
    daytrade_status.set_data_dir(DATA_DIR)
    items = load_symbols()
    runtime_config = load_runtime_config()
    symbol_states = []
    now = datetime.now()
    for item in items:
        symbol = str(item.get("symbol", ""))
        st = current_freshness_state(load_state(symbol), load_settings(symbol), now)
        symbol_states.append({
            "symbol": symbol,
            "position": st.get("current_position_today", "FLAT"),
            "signal": st.get("signal", ""),
            "action": st.get("last_action", ""),
            "data_freshness_status": st.get("data_freshness_status", ""),
            "latest_ts": st.get("latest_ts", ""),
            "last_runner_at": st.get("last_runner_at", ""),
            "live_order_enabled": False,
        })
    return daytrade_status.build_http_status(symbol_states, runtime_config, now)


def build_device_payload():
    props = {}
    for key in DEVICE_PROP_KEYS:
        value = ""
        try:
            result = subprocess.run(
                ["getprop", key],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
            )
            value = result.stdout.strip()
        except Exception:
            value = ""
        props[key] = value
    return {
        "ok": True,
        "source": "android_getprop" if any(props.values()) else "unavailable",
        "props": props,
    }


def build_pc_wake_payload():
    payload = load_json(PC_WAKE_FILE, {})
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("ok", bool(payload))
    return payload


class Handler(BaseHTTPRequestHandler):
    def send_response_content(self, status, content_type, body, include_body=True):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if include_body:
                self.wfile.write(body)
        except CLIENT_DISCONNECT_ERRORS:
            self.log_message("client disconnected before response completed")

    def do_GET(self):
        if self.path in ["/status.json", "/health"]:
            payload = build_status_payload()
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response_content(200, "application/json; charset=utf-8", body)
            return
        if self.path == "/device.json":
            payload = build_device_payload()
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response_content(200, "application/json; charset=utf-8", body)
            return
        if self.path == "/pc-wake.json":
            payload = build_pc_wake_payload()
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response_content(200, "application/json; charset=utf-8", body)
            return
        if self.path not in ["/", "/index.html"]:
            body = b"not found\n"
            self.send_response_content(404, "text/plain; charset=utf-8", body)
            return
        body = build_page().encode("utf-8")
        self.send_response_content(200, "text/html; charset=utf-8", body)

    def do_POST(self):
        if self.path != "/feed/bars":
            body = json.dumps({"ok": False, "error": "not found"}).encode("utf-8")
            self.send_response_content(404, "application/json; charset=utf-8", body)
            return

        expected_token = os.environ.get("DAYTRADE_FEED_TOKEN", "").strip()
        if expected_token and self.headers.get("X-Daytrade-Feed-Token", "").strip() != expected_token:
            body = json.dumps({"ok": False, "error": "unauthorized"}).encode("utf-8")
            self.send_response_content(401, "application/json; charset=utf-8", body)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except Exception:
            length = 0
        if length <= 0 or length > 1024 * 1024:
            body = json.dumps({"ok": False, "error": "invalid content length"}).encode("utf-8")
            self.send_response_content(400, "application/json; charset=utf-8", body)
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = accept_feed_bars(payload)
            status = 200 if result.get("ok") else 400
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response_content(status, "application/json; charset=utf-8", body)
        except Exception as exc:
            daytrade_status.set_data_dir(DATA_DIR)
            daytrade_status.write_feed_status(
                adapter="remote_feed",
                status="error",
                message="remote feed post failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            body = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}).encode("utf-8")
            self.send_response_content(500, "application/json; charset=utf-8", body)

    def do_HEAD(self):
        if self.path not in ["/", "/index.html"]:
            body = b"not found\n"
            self.send_response_content(404, "text/plain; charset=utf-8", body, include_body=False)
            return
        body = build_page().encode("utf-8")
        self.send_response_content(200, "text/html; charset=utf-8", body, include_body=False)

    def log_message(self, format, *args):
        print("[%s] %s" % (self.log_date_time_string(), format % args))


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    httpd = AppHTTPServer((HOST, PORT), Handler)
    print(f"daytrade_app started: http://{HOST}:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
