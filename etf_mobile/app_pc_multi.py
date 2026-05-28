from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta
import csv
import html
import json
import re

DATA_DIR = Path("data")
SYMBOLS_FILE = DATA_DIR / "symbols.json"

HOST = "0.0.0.0"
PORT = 8000
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
    if not rows:
        return [{"symbol": x} for x in ["1321", "1306", "1343", "1540", "2510"]]
    return [x for x in rows if x.get("enabled", True) is not False]


def load_state(symbol: str):
    path = DATA_DIR / symbol / "paper_state_atr.json"
    return load_json(path, {"symbol": symbol, "display_note": "state file missing"})


def parse_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return None


def simple_trading_day(day):
    if day.weekday() >= 5:
        return False
    if day.month == 1 and day.day in (1, 2, 3):
        return False
    if day.month == 12 and day.day == 31:
        return False
    return True


def previous_trading_day(day):
    cur = day
    while True:
        cur = cur - timedelta(days=1)
        if simple_trading_day(cur):
            return cur


def expected_data_date(now):
    today = now.date()
    if simple_trading_day(today) and (now.hour, now.minute) >= (18, 5):
        return today
    cur = today
    while True:
        cur = cur - timedelta(days=1)
        if simple_trading_day(cur):
            return cur


def state_data_health(st: dict, now=None):
    now = now or datetime.now()
    expected = parse_date(st.get("data_expected_date")) or expected_data_date(now)
    latest_state = parse_date(st.get("latest_date"))
    latest_ohlc = parse_date(st.get("data_latest_ohlc_date")) or latest_state
    status = str(st.get("data_freshness_status", "")).strip()
    source = str(st.get("price_source", "")).strip()

    latest = latest_ohlc or latest_state
    if status in ("missing_ohlc", "stale_ohlc") or source in ("missing_ohlc", "stale_ohlc"):
        ok = False
    elif latest is None:
        ok = False
        status = "missing_ohlc"
    else:
        ok = latest >= expected
        status = "ok" if ok else "stale_ohlc"

    label = "OK"
    if not ok:
        label = f"DATA STALE latest={latest.isoformat() if latest else '-'} expected={expected.isoformat()}"
    return {
        "ok": ok,
        "status": status or ("ok" if ok else "stale_ohlc"),
        "label": label,
        "latest": latest.isoformat() if latest else "",
        "expected": expected.isoformat(),
    }


def load_history(symbol: str):
    path = DATA_DIR / symbol / "paper_history.csv"
    if not path.exists():
        return []

    rows = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_s = str(row.get("date", "")).strip()
                eq_s = str(row.get("equity", "")).strip()
                if not date_s or not eq_s:
                    continue
                try:
                    equity = float(eq_s)
                except Exception:
                    continue
                rows.append({"date": date_s, "equity": equity, "value": equity - 1.0})
    except Exception:
        return []

    uniq = {}
    for row in rows:
        uniq[row["date"]] = row

    return [uniq[k] for k in sorted(uniq.keys())]


def format_yen(value):
    try:
        return f"¥{float(value):,.0f}"
    except Exception:
        return "-"


def format_pct_from_rate(rate):
    try:
        return f"{float(rate) * 100:.2f}%"
    except Exception:
        return "-"


def format_number(value, decimals=4, thousands=False):
    try:
        x = float(value)
    except Exception:
        return "-"

    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}" if thousands else str(int(round(x)))

    text = f"{x:,.{decimals}f}" if thousands else f"{x:.{decimals}f}"
    return text.rstrip("0").rstrip(".")


def format_price(value):
    return format_number(value, decimals=1, thousands=True)


def format_param(value):
    return format_number(value, decimals=4, thousands=False)


def calc_history_stats(rows):
    if not rows:
        return {
            "period": "-",
            "count": "-",
            "pnl_rate": "-",
            "max_drawdown": "-",
        }

    values = []
    dates = []
    for row in rows:
        try:
            values.append(float(row.get("equity", float(row["value"]) + 1.0)))
            dates.append(str(row["date"]))
        except Exception:
            continue

    if not values:
        return {
            "period": "-",
            "count": "-",
            "pnl_rate": "-",
            "max_drawdown": "-",
        }

    if dates[0] == dates[-1]:
        period = dates[0]
    else:
        period = f"{dates[0]} - {dates[-1]}"

    start_value = values[0]
    last_value = values[-1]
    pnl_rate = (last_value / start_value - 1.0) if start_value else None

    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        if value > peak:
            peak = value
        if peak:
            drawdown = value / peak - 1.0
            if drawdown < max_drawdown:
                max_drawdown = drawdown

    return {
        "period": period,
        "count": f"{len(values)}件",
        "pnl_rate": format_pct_from_rate(pnl_rate),
        "max_drawdown": format_pct_from_rate(max_drawdown),
    }


def jp_position_label(value):
    mapping = {
        "CASH": "現金待機",
        "LONG": "保有中",
        "FLAT": "現金待機",
        "NONE": "なし",
    }
    text = str(value).strip() if value is not None else ""
    return mapping.get(text, text if text else "-")


def jp_action_label(value):
    mapping = {
        "BUY_NEXT_OPEN": "次回始値で買い",
        "SELL_NEXT_OPEN": "次回始値で売り",
        "HOLD_CASH": "現金維持",
        "HOLD_LONG": "保有継続",
        "NONE": "変更なし",
        "BUY": "買い",
        "SELL": "売り",
    }
    text = str(value).strip() if value is not None else ""
    return mapping.get(text, text if text else "-")


def jp_market_status_label(value):
    mapping = {
        "TRADING_DAY": "営業日",
        "HOLIDAY": "休場日",
        "MARKET_CLOSED": "取引時間外",
        "MARKET_OPEN": "取引中",
        "本日は取引日": "本日は取引日",
        "本日は休場日": "本日は休場日",
    }
    text = str(value).strip() if value is not None else ""
    return mapping.get(text, text if text else "-")


def jp_source_label(value):
    mapping = {
        "ohlc.csv": "実OHLC",
        "dummy_calendar": "休場日補完",
    }
    text = str(value).strip() if value is not None else ""
    if text.startswith("rule_"):
        return "MA/勢い/ATR"
    if text == "disabled_by_settings":
        return "停止中"
    return mapping.get(text, text if text else "-")


def normalize_display_text(text):
    if text is None:
        return "-"
    text = str(text).strip()
    if not text:
        return "-"

    replacements = [
        ("dummy fallback", "休場日/未取得日の補完"),
        ("予測はテスト用ダミーです", "予測は休場日/未取得日の補完です"),
        ("価格もダミー更新", "価格も補完更新"),
        ("価格はohlc.csvを使用", "価格は実OHLCを使用"),
        ("ohlc.csv", "実OHLC"),
        ("momentum強", "勢い強"),
        ("momentum正", "勢い正"),
        ("momentum弱", "勢い弱"),
        ("momentum負", "勢い負"),
        ("momentum不足", "勢い不足"),
        ("BUY_NEXT_OPEN", "次回始値で買い"),
        ("SELL_NEXT_OPEN", "次回始値で売り"),
        ("HOLD_CASH", "現金維持"),
        ("HOLD_LONG", "保有継続"),
        ("MARKET_CLOSED", "取引時間外"),
        ("MARKET_OPEN", "取引中"),
        ("TRADING_DAY", "営業日"),
        ("HOLIDAY", "休場日"),
        ("CASH", "現金待機"),
        ("LONG", "保有中"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)

    text = re.sub(r"(\d{4}-\d{2}-\d{2})\s*に\s*次回始値で買い\s*予定", r"\1 寄りで買い予定", text)
    text = re.sub(r"(\d{4}-\d{2}-\d{2})\s*に\s*次回始値で売り\s*予定", r"\1 寄りで売り予定", text)
    text = re.sub(r"(\d{4}-\d{2}-\d{2})\s*も\s*保有中\s*継続予定", r"\1 も保有継続予定", text)
    text = re.sub(r"(\d{4}-\d{2}-\d{2})\s*も\s*現金待機\s*継続予定", r"\1 も現金維持予定", text)
    text = text.replace("今日は 保有中", "今日は保有中")
    text = text.replace("今日は 現金待機", "今日は現金待機")
    text = text.replace("今日は保有中継続", "今日は保有継続")
    text = text.replace("今日は現金待機継続", "今日は現金維持")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_today_status_text(st):
    display = str(st.get("display_today_status", "")).strip()
    if display:
        return normalize_display_text(display)

    pos = jp_position_label(st.get("current_position_today"))
    stop_price = st.get("current_stop_today")
    stop_text = ""
    if stop_price not in [None, "", "-"]:
        stop_text = f" / stop {format_price(stop_price)}"

    if pos == "-" and not stop_text:
        return "-"
    return f"{pos}{stop_text}"


def make_next_plan_text(st):
    display = str(st.get("display_next_plan", "")).strip()
    if display:
        return normalize_display_text(display)

    eff = str(st.get("effective_from_date", "")).strip()
    action = str(st.get("pending_action", "")).strip()

    if action == "BUY_NEXT_OPEN":
        return f"{eff} 寄りで買い予定" if eff else "次回始値で買い"
    if action == "SELL_NEXT_OPEN":
        return f"{eff} 寄りで売り予定" if eff else "次回始値で売り"
    if action == "HOLD_LONG":
        return f"{eff} も保有継続予定" if eff else "保有継続"
    if action == "HOLD_CASH":
        return f"{eff} も現金維持予定" if eff else "現金維持"

    next_pos = jp_position_label(st.get("next_position"))
    return next_pos if next_pos != "-" else "-"


def build_asset_svg(rows, width=760, height=240):
    if len(rows) < 2:
        return """
        <div class="chart-empty">
            履歴が2件以上たまると損益率グラフを表示します
        </div>
        """

    if len(rows) < 2:
        return """
        <div class="chart-empty">
            履歴がまだ少ないため、損益率グラフは表示待ちです
        </div>
        """

    left = 60
    right = 28
    top = 16
    bottom = 28
    plot_w = width - left - right
    plot_h = height - top - bottom

    values = [float(x["value"]) for x in rows]
    dates = [x["date"] for x in rows]

    v_min = min(values)
    v_max = max(values)
    if abs(v_max - v_min) < 1e-9:
        pad = max(abs(v_max) * 0.01, 100.0)
        v_min -= pad
        v_max += pad
    else:
        diff = v_max - v_min
        center = (v_max + v_min) / 2.0
        pad = max(diff * 0.2, center * 0.0005)
        v_min -= pad
        v_max += pad

    def x_pos(i):
        if len(values) == 1:
            return left + plot_w / 2
        return left + (plot_w * i / (len(values) - 1))

    def y_pos(v):
        return top + (v_max - v) / (v_max - v_min) * plot_h

    points = " ".join(f"{x_pos(i):.1f},{y_pos(v):.1f}" for i, v in enumerate(values))

    grid_lines = []
    label_texts = []
    for k in range(5):
        frac = k / 4
        y = top + plot_h * frac
        val = v_max - (v_max - v_min) * frac
        grid_lines.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e8edf3" stroke-width="1" />'
        )
        label_texts.append(
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#6b7280">{html.escape(format_pct_from_rate(val))}</text>'
        )

    x_labels = []
    idxs = sorted(set([0, len(dates) // 2, len(dates) - 1]))
    for idx in idxs:
        x = x_pos(idx)
        anchor = "middle"
        if idx == 0:
            anchor = "start"
        elif idx == len(dates) - 1:
            anchor = "end"
        x_labels.append(
            f'<text x="{x:.1f}" y="{height - 8}" text-anchor="{anchor}" font-size="11" fill="#6b7280">{html.escape(dates[idx])}</text>'
        )

    last_x = x_pos(len(values) - 1)
    last_y = y_pos(values[-1])

    return f"""
    <svg class="asset-svg" viewBox="0 0 {width} {height}" preserveAspectRatio="none">
        <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" rx="12" />
        {''.join(grid_lines)}
        {''.join(label_texts)}
        <polyline fill="none" stroke="#2563eb" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="{points}" />
        <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4.5" fill="#2563eb" />
        {''.join(x_labels)}
    </svg>
    """


def row_html(label, value):
    return f'<div class="row"><span class="label">{html.escape(label)}</span><span class="value">{html.escape(value)}</span></div>'


def render_card(symbol: str):
    st = load_state(symbol)
    hist = load_history(symbol)
    stats = calc_history_stats(hist)

    now = datetime.now()
    page_updated = now.strftime("%Y-%m-%d %H:%M:%S")
    graph_html = build_asset_svg(hist)
    pred_prob_text = format_number(st.get("pred_prob"), decimals=4)
    health = state_data_health(st, now)
    stale_warning = ""
    if not health["ok"]:
        stale_warning = (
            '<div class="data-warning">'
            f'{html.escape(health["label"])} / evaluation paused'
            '</div>'
        )

    primary_rows = [
        row_html("データ", str(st.get("latest_date", "-"))),
        row_html("鮮度", health["label"]),
        row_html("市場", jp_market_status_label(st.get("display_market_status", st.get("market_status", "-")))),
        row_html("今日", make_today_status_text(st)),
        row_html("次回", make_next_plan_text(st)),
        row_html("累計損益", format_pct_from_rate(st.get("sim_pnl_rate"))),
        row_html("最大DD", stats["max_drawdown"]),
    ]

    detail_rows = [
        row_html("画面更新", page_updated),
        row_html("自動実行", str(st.get("last_runner_at", "-"))),
        row_html("戦績期間", stats["period"]),
        row_html("履歴件数", stats["count"]),
        row_html("履歴損益", stats["pnl_rate"]),
        row_html("予測確率", pred_prob_text),
        row_html("予測元", jp_source_label(st.get("pred_source"))),
        row_html("価格元", jp_source_label(st.get("price_source"))),
        row_html("判定理由", normalize_display_text(st.get("strategy_reason"))),
        row_html("終値", format_price(st.get("close"))),
        row_html("移動平均", format_price(st.get("trend_ma"))),
        row_html("勢い", format_pct_from_rate(st.get("momentum_return"))),
        row_html("ATR率", format_pct_from_rate(st.get("atr_rate"))),
        row_html("売買コスト", format_pct_from_rate(st.get("trade_cost_rate"))),
        row_html("買い閾値", format_param(st.get("buy_th"))),
        row_html("売り閾値", format_param(st.get("sell_th"))),
        row_html("最大投入", format_pct_from_rate(st.get("max_position_w"))),
        row_html("目標比率", format_pct_from_rate(st.get("target_w"))),
    ]

    note = normalize_display_text(st.get("display_note", ""))
    note_html = "" if note == "-" else f'<div class="note">{html.escape(note)}</div>'

    return f"""
    <div class="card">
        <div class="card-title">{html.escape(symbol)}</div>
        {stale_warning}
        <div class="primary-rows">{''.join(primary_rows)}</div>
        <div class="chart-title">損益率推移</div>
        {graph_html}
        <details class="details">
            <summary>詳細</summary>
            {''.join(detail_rows)}
            {note_html}
        </details>
    </div>
    """


def build_page():
    cards = []
    for item in load_symbols():
        symbol = str(item["symbol"])
        cards.append(render_card(symbol))

    return f"""
    <!doctype html>
    <html lang="ja">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>日本ETF ペーパートレード表示</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                font-family: sans-serif;
                margin: 0;
                background: #f4f7fb;
                color: #111827;
            }}
            .page {{
                padding: 18px;
                max-width: 1520px;
                margin: 0 auto;
            }}
            .title {{
                font-size: 28px;
                font-weight: 700;
                margin-bottom: 8px;
            }}
            .desc {{
                color: #4b5563;
                font-size: 14px;
                margin-bottom: 16px;
                line-height: 1.6;
            }}
            .grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(min(100%, 430px), 1fr));
                gap: 18px;
                align-items: start;
            }}
            .card {{
                background: #ffffff;
                border: 1px solid #d7dee8;
                border-radius: 8px;
                padding: 16px;
                box-shadow: 0 2px 12px rgba(0,0,0,0.04);
            }}
            .card-title {{
                font-size: 22px;
                font-weight: 700;
                margin-bottom: 12px;
            }}
            .data-warning {{
                border: 1px solid #fecaca;
                background: #fef2f2;
                color: #991b1b;
                border-radius: 8px;
                padding: 9px 10px;
                margin-bottom: 12px;
                font-size: 13px;
                font-weight: 700;
                line-height: 1.45;
                overflow-wrap: anywhere;
            }}
            .row {{
                display: grid;
                grid-template-columns: 110px 1fr;
                gap: 10px;
                margin: 4px 0;
                line-height: 1.5;
            }}
            .primary-rows .row {{
                margin: 6px 0;
                font-size: 15px;
            }}
            .label {{
                color: #6b7280;
            }}
            .value {{
                min-width: 0;
                overflow-wrap: anywhere;
            }}
            .chart-title {{
                margin-top: 14px;
                margin-bottom: 6px;
                font-weight: 700;
            }}
            .asset-svg {{
                width: 100%;
                height: 220px;
                display: block;
                border-radius: 8px;
                border: 1px solid #e5e7eb;
                background: #fff;
            }}
            .chart-empty {{
                height: 220px;
                border: 1px dashed #d1d5db;
                border-radius: 8px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #6b7280;
                background: #fafafa;
                text-align: center;
                padding: 12px;
            }}
            .note {{
                margin-top: 10px;
                font-size: 13px;
                color: #6b7280;
                min-height: 1.2em;
            }}
            .details {{
                margin-top: 10px;
                border-top: 1px solid #edf1f6;
                padding-top: 8px;
            }}
            .details summary {{
                cursor: pointer;
                color: #374151;
                font-weight: 700;
                user-select: none;
            }}
            .details .row {{
                font-size: 13px;
                grid-template-columns: 96px 1fr;
            }}
            @media (max-width: 720px) {{
                .page {{ padding: 12px; }}
                .row {{ grid-template-columns: 96px 1fr; gap: 8px; }}
                .asset-svg, .chart-empty {{ height: 200px; }}
            }}
        </style>
        <script>
            setTimeout(() => location.reload(), 10000);
        </script>
    </head>
    <body>
        <div class="page">
            <div class="title">日本ETF ペーパートレード表示</div>
            <div class="desc">
                10秒ごとに再読込します。
            </div>
            <div class="grid">{''.join(cards)}</div>
        </div>
    </body>
    </html>
    """


def build_status_payload():
    now = datetime.now()
    symbols = []
    for item in load_symbols():
        symbol = str(item["symbol"])
        st = load_state(symbol)
        health = state_data_health(st, now)
        symbols.append({
            "symbol": symbol,
            "latest_date": str(st.get("latest_date", "")),
            "last_runner_at": str(st.get("last_runner_at", "")),
            "position": str(st.get("current_position_today", "")),
            "next_position": str(st.get("next_position", "")),
            "price_source": str(st.get("price_source", "")),
            "data_freshness_status": health["status"],
            "data_ok": health["ok"],
            "data_latest": health["latest"],
            "data_expected": health["expected"],
        })
    stale = [row for row in symbols if not row["data_ok"]]
    return {
        "ok": len(stale) == 0,
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "stale_count": len(stale),
        "symbols": symbols,
    }


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

    def send_body(self, status, content_type, body):
        self.send_response_content(status, content_type, body, include_body=True)

    def send_headers_only(self, status, content_type, body):
        self.send_response_content(status, content_type, body, include_body=False)

    def do_GET(self):
        if self.path in ["/health", "/status.json"]:
            body = json.dumps(build_status_payload(), ensure_ascii=False, indent=2).encode("utf-8")
            self.send_body(200, "application/json; charset=utf-8", body)
            return

        if self.path not in ["/", "/index.html"]:
            body = b"not found\n"
            self.send_body(404, "text/plain; charset=utf-8", body)
            return

        body = build_page().encode("utf-8")
        self.send_body(200, "text/html; charset=utf-8", body)

    def do_HEAD(self):
        if self.path not in ["/", "/index.html"]:
            body = b"not found\n"
            self.send_headers_only(404, "text/plain; charset=utf-8", body)
            return

        body = build_page().encode("utf-8")
        self.send_headers_only(200, "text/html; charset=utf-8", body)

    def log_message(self, format, *args):
        print("[%s] %s" % (self.log_date_time_string(), format % args))


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


httpd = AppHTTPServer((HOST, PORT), Handler)
print(f"app_pc_multi started: http://{HOST}:{PORT}")
httpd.serve_forever()
