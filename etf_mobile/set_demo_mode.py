from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path


DATA_DIR = Path("data")
SYMBOLS_FILE = DATA_DIR / "symbols.json"
OPTIMIZATION_RESULTS = Path("logs") / "optimization_results.json"


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_symbols() -> list[str]:
    rows = read_json(SYMBOLS_FILE, [])
    symbols = []
    for row in rows:
        symbol = str(row.get("symbol", "")).strip()
        if symbol and row.get("enabled", True) is not False:
            symbols.append(symbol)
    return symbols


def settings_path(symbol: str) -> Path:
    return DATA_DIR / symbol / "paper_settings.json"


def load_settings(symbol: str) -> dict:
    return read_json(settings_path(symbol), {"symbol": symbol})


def save_settings(symbol: str, settings: dict):
    path = settings_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings["symbol"] = symbol
    write_json(path, settings)


def enable_all(max_position: float):
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed = []
    for symbol in load_symbols():
        settings = load_settings(symbol)
        settings["strategy_enabled"] = True
        settings["max_position_w"] = max(0.0, min(1.0, max_position))
        settings["demo_mode"] = "all_candidates"
        settings["demo_enabled_at"] = now
        save_settings(symbol, settings)
        changed.append(symbol)
    print(f"enabled_all={len(changed)}")
    print("symbols=" + ",".join(changed))


def restore_optimized():
    payload = read_json(OPTIMIZATION_RESULTS, {})
    results = payload.get("results", payload if isinstance(payload, list) else [])
    restored = []
    if not results:
        raise SystemExit(f"no optimization results found: {OPTIMIZATION_RESULTS}")

    for result in results:
        symbol = str(result.get("symbol", "")).strip()
        settings = result.get("settings") or {}
        if not symbol or not settings:
            continue
        settings = dict(settings)
        settings.pop("demo_mode", None)
        settings.pop("demo_enabled_at", None)
        save_settings(symbol, settings)
        restored.append(symbol)

    print(f"restored={len(restored)}")
    print("symbols=" + ",".join(restored))


def print_status():
    active = []
    inactive = []
    demo = []
    for symbol in load_symbols():
        settings = load_settings(symbol)
        if settings.get("strategy_enabled", True) is not False:
            active.append(symbol)
        else:
            inactive.append(symbol)
        if settings.get("demo_mode"):
            demo.append(symbol)

    print(f"active={len(active)} inactive={len(inactive)} demo={len(demo)}")
    print("active_symbols=" + ",".join(active))
    if inactive:
        print("inactive_symbols=" + ",".join(inactive))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    all_on = sub.add_parser("all-on")
    all_on.add_argument("--max-position", type=float, default=0.2)

    sub.add_parser("optimized")
    sub.add_parser("status")

    args = parser.parse_args()
    if args.command == "all-on":
        enable_all(args.max_position)
    elif args.command == "optimized":
        restore_optimized()
    else:
        print_status()


if __name__ == "__main__":
    main()
