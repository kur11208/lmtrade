from __future__ import annotations

import argparse
import csv

import daytrade_runner as runner


def load_alerts() -> list[dict]:
    rows = []
    for item in runner.load_symbols():
        symbol = str(item["symbol"])
        path = runner.DATA_DIR / symbol / "alerts_daytrade.csv"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                row["symbol"] = row.get("symbol") or symbol
                rows.append(row)
    rows.sort(key=lambda row: str(row.get("timestamp", "")))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--last", type=int, default=20)
    args = parser.parse_args()

    rows = load_alerts()[-max(1, args.last):]
    for row in rows:
        print(
            f"{row.get('timestamp')} {row.get('symbol')} "
            f"{row.get('signal')} {row.get('action')} "
            f"price={row.get('price')} reason={row.get('reason')}"
        )


if __name__ == "__main__":
    main()
