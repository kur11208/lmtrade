"""Read-only, deterministic presentation of the existing paper-trading engine."""

from __future__ import annotations

import argparse
import csv
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import socket
import webbrowser

from daytrade_backtest import max_drawdown, replay_bars
import daytrade_runner as runner


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "interview_fixtures"
WEB = ROOT / "interview_web"
SCENARIOS = (
    ("trend", "上昇", "DEMO-A"),
    ("reversal", "下抜け後の反転", "DEMO-B"),
    ("range", "レンジ", "DEMO-C"),
)


class DemoHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        # Windows otherwise allows two HTTP servers to share one listening port.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def load_fixture(path: Path) -> list[dict]:
    """Fail visibly on corrupt data rather than silently repairing demo input."""
    with path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError(f"Empty fixture: {path.name}")
    bars = []
    for row in rows:
        values = [float(row[key]) for key in ("open", "high", "low", "close", "volume")]
        o, h, low, c, volume = values
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"Non-finite value: {path.name}")
        if min(o, h, low, c) <= 0 or low > min(o, c) or h < max(o, c) or volume < 0:
            raise ValueError(f"Invalid OHLCV: {path.name}")
        bar = runner.normalize_bar(row)
        if bar is None or (bars and bar["ts"] <= bars[-1]["ts"]):
            raise ValueError(f"Invalid or unordered timestamp: {path.name}")
        bars.append(bar)
    return bars


def build_scenario(key: str, title: str, symbol: str, settings: dict) -> dict:
    bars = load_fixture(FIXTURES / f"{key}.csv")
    states = list(replay_bars(symbol, bars, settings))
    capital = settings["sim_capital_yen"]
    frames, fills, trades = [], [], []
    for index, (bar, state) in enumerate(zip(bars, states)):
        signal = state["signal"]
        if signal in ("ENTRY", "EXIT"):
            fills.append({
                "timestamp": bar["timestamp"], "index": index, "signal": signal,
                "action": state["last_action"], "reason": state["strategy_reason"],
                "price": state["entry_price"] if signal == "ENTRY" else state["exit_price"],
                "weight": state["current_w_today"] if signal == "ENTRY" else state["last_exit_w"],
            })
        if len(state["recent_trades"]) > len(trades):
            entry_frame = next(row for row in frames if row["timestamp"] == state["recent_trades"][-1]["entry_ts"])
            prior_equity = states[entry_frame["index"]]["trade_start_equity"]
            trade = dict(state["recent_trades"][-1])
            trade.update({
                "entry_price": entry_frame["entry_price"],
                "weight": entry_frame["weight"],
                "pnl_yen": capital * (state["equity"] - prior_equity),
            })
            trades.append(trade)
        frames.append({
            "index": index, "timestamp": bar["timestamp"],
            "open": bar["open"], "high": bar["high"], "low": bar["low"],
            "close": bar["close"], "volume": bar["volume"],
            "vwap": state["vwap"], "or_high": state["opening_range_high"],
            "or_low": state["opening_range_low"],
            "equity": state["equity"], "balance_yen": capital * state["equity"],
            "pnl_yen": capital * (state["equity"] - 1.0),
            "signal": signal, "action": state["last_action"],
            "reason": state["strategy_reason"],
            "position": state["current_position_today"], "weight": state["current_w_today"],
            "entry_price": state.get("entry_price"), "stop_price": state.get("stop_price"),
            "target_price": state.get("target_price"),
            "closed_trades": state["closed_trade_count"],
            "wins": state["win_count"], "losses": state["loss_count"],
            "drawdown": max_drawdown([1.0] + [s["equity"] for s in states[:index + 1]]),
        })
    final = states[-1]
    if final["current_position_today"] != "FLAT":
        raise ValueError(f"Fixture ended with an open position: {key}")
    return {"id": key, "title": title, "symbol": symbol, "frames": frames,
            "fills": fills, "trades": trades}


def build_demo() -> dict:
    settings = json.loads((FIXTURES / "settings.json").read_text(encoding="utf-8"))
    if set(settings) != set(runner.DEFAULT_SETTINGS):
        raise ValueError("Fixture settings must explicitly pin every engine setting")
    if settings["use_market_filter"] or settings["partial_exit_rate"] != 0:
        raise ValueError("Interview replay requires one instrument and full exits")
    body = {
        "schema_version": 1,
        "data_source": "synthetic_fixed_csv",
        "trading_mode": "paper", "order_adapter": "paper", "allow_live_order": False,
        "settings": settings,
        "scenarios": [build_scenario(*scenario, settings) for scenario in SCENARIOS],
    }
    body["fingerprint"] = hashlib.sha256(encode_json(body)).hexdigest()
    return body


def encode_json(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def create_server(port: int = 8765) -> ThreadingHTTPServer:
    """Compute before accepting requests; HTTP can only read fixed results/assets."""
    payload = encode_json(build_demo())
    routes = {
        "/": ((WEB / "index.html").read_bytes(), "text/html; charset=utf-8"),
        "/demo.css": ((WEB / "demo.css").read_bytes(), "text/css; charset=utf-8"),
        "/demo.js": ((WEB / "demo.js").read_bytes(), "text/javascript; charset=utf-8"),
        "/api/demo": (payload, "application/json; charset=utf-8"),
        "/download.json": (payload, "application/json; charset=utf-8"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(False)

        def do_HEAD(self):
            self.respond(True)

        def respond(self, head: bool):
            route = routes.get(self.path)
            if route is None:
                self.send_error(404)
                return
            data, content_type = route
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            if self.path == "/download.json":
                self.send_header("Content-Disposition", 'attachment; filename="lmtrade-demo.json"')
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    return DemoHTTPServer(("127.0.0.1", port), Handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true", help="verify replay determinism without starting HTTP")
    args = parser.parse_args()
    if args.check:
        first = build_demo()
        if encode_json(first) != encode_json(build_demo()):
            raise RuntimeError("Replay is not deterministic")
        print(json.dumps({"fingerprint": first["fingerprint"], "allow_live_order": False,
                          "results": [{"id": s["id"], "pnl_yen": s["frames"][-1]["pnl_yen"],
                                       "trades": len(s["trades"])} for s in first["scenarios"]]}, indent=2))
        return 0
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    server = None
    # Never terminate another application when the default port is occupied.
    candidates = range(8765, 8775) if args.port == 8765 else [args.port]
    for candidate in candidates:
        try:
            server = create_server(candidate)
            break
        except OSError as error:
            if error.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
    if server is None:
        parser.error("Port unavailable. Try --port 8888")
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"LMTrade offline paper demo: {url}", flush=True)
    print("Stop with Ctrl+C. No account, market feed or scheduled tasks are used.", flush=True)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            print("Open the URL above in your browser.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
