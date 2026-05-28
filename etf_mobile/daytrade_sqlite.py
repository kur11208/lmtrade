from __future__ import annotations

from pathlib import Path
import argparse
import sqlite3

import daytrade_runner as runner

DB_FILE = runner.DATA_DIR / "daytrade.sqlite"


def connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        create table if not exists intraday_bars (
            symbol text not null,
            timestamp text not null,
            date text not null,
            open real not null,
            high real not null,
            low real not null,
            close real not null,
            volume real not null,
            primary key (symbol, timestamp)
        )
        """
    )
    con.execute(
        """
        create table if not exists paper_history (
            symbol text not null,
            timestamp text not null,
            equity real not null,
            position text,
            weight real,
            price real,
            action text,
            signal text,
            reason text,
            primary key (symbol, timestamp)
        )
        """
    )
    return con


def import_bars(con, symbol: str) -> int:
    rows = runner.load_intraday_bars(symbol)
    con.executemany(
        """
        insert or replace into intraday_bars
        (symbol, timestamp, date, open, high, low, close, volume)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                symbol,
                row["timestamp"],
                row["date"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
            )
            for row in rows
        ],
    )
    return len(rows)


def import_history(con, symbol: str) -> int:
    path = runner.DATA_DIR / symbol / "paper_history_daytrade.csv"
    if not path.exists():
        return 0
    import csv

    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            timestamp = str(row.get("timestamp", "")).strip()
            if not timestamp:
                continue
            rows.append((
                symbol,
                timestamp,
                runner.parse_float(row.get("equity"), 1.0),
                row.get("position", ""),
                runner.parse_float(row.get("weight"), 0.0),
                runner.parse_float(row.get("price"), 0.0),
                row.get("action", ""),
                row.get("signal", ""),
                row.get("reason", ""),
            ))
    con.executemany(
        """
        insert or replace into paper_history
        (symbol, timestamp, equity, position, weight, price, action, signal, reason)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default=str(DB_FILE))
    args = parser.parse_args()

    con = connect(Path(args.db))
    try:
        total_bars = 0
        total_history = 0
        for item in runner.load_symbols():
            symbol = str(item["symbol"])
            total_bars += import_bars(con, symbol)
            total_history += import_history(con, symbol)
        con.commit()
        print(f"imported bars={total_bars} history={total_history} db={args.db}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
