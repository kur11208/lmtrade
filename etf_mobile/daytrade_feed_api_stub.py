from __future__ import annotations

import argparse

import daytrade_config
import daytrade_market_data
import daytrade_runner
import daytrade_status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default="")
    args = parser.parse_args()

    cfg = daytrade_config.load_runtime_config()
    daytrade_status.set_data_dir(daytrade_runner.DATA_DIR)
    adapter_name = args.adapter or cfg.get("market_data_adapter", "csv")
    symbols = [str(item.get("symbol")) for item in daytrade_runner.load_symbol_universe()]
    adapter = daytrade_market_data.get_adapter(adapter_name)
    result = adapter.fetch_latest_bars(symbols)

    daytrade_status.write_feed_status(
        adapter=result.adapter,
        status=result.status,
        message=result.message,
        symbols=symbols,
        bars_written=result.bars_written,
    )

    print(f"market_data_adapter={result.adapter}")
    print("api_usage=market_data_only")
    print("live_order=false")
    print(f"status={result.status}")
    print(result.message)


if __name__ == "__main__":
    main()
