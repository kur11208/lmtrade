from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv


@dataclass
class AdapterResult:
    adapter: str
    status: str
    message: str
    rows_by_symbol: dict[str, list[dict]]

    @property
    def bars_written(self) -> int:
        return sum(len(rows) for rows in self.rows_by_symbol.values())


class CsvMarketDataAdapter:
    name = "csv"

    def fetch_latest_bars(self, symbols: list[str]) -> AdapterResult:
        return AdapterResult(
            adapter=self.name,
            status="external_file",
            message="CSV files are the market data source; no API fetch is performed.",
            rows_by_symbol={symbol: [] for symbol in symbols},
        )


class DemoCsvMarketDataAdapter(CsvMarketDataAdapter):
    name = "demo_csv"


class ApiMarketDataAdapter:
    name = "api"

    def fetch_latest_bars(self, symbols: list[str]) -> AdapterResult:
        return AdapterResult(
            adapter=self.name,
            status="not_configured",
            message="API adapter skeleton is present, but no market-data credentials/source are configured.",
            rows_by_symbol={symbol: [] for symbol in symbols},
        )


def get_adapter(name: str):
    normalized = str(name or "csv").strip().lower()
    if normalized in ("csv", "file"):
        return CsvMarketDataAdapter()
    if normalized in ("demo", "demo_csv"):
        return DemoCsvMarketDataAdapter()
    if normalized in ("api", "rakuten", "rss"):
        return ApiMarketDataAdapter()
    return CsvMarketDataAdapter()


def write_bars(path: Path, rows: list[dict], replace: bool = False) -> int:
    by_ts = {}
    if not replace and path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                ts = str(row.get("timestamp", "")).strip()
                if ts:
                    by_ts[ts] = row
    for row in rows:
        ts = str(row.get("timestamp", "")).strip()
        if ts:
            by_ts[ts] = row

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for ts in sorted(by_ts):
            row = by_ts[ts]
            writer.writerow({
                "timestamp": row.get("timestamp", ""),
                "open": row.get("open", ""),
                "high": row.get("high", ""),
                "low": row.get("low", ""),
                "close": row.get("close", ""),
                "volume": row.get("volume", 0),
            })
    return len(by_ts)
