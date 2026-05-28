from __future__ import annotations

import argparse

import daytrade_order_queue as queue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    rows = queue.pending_orders()
    for row in rows:
        print(
            f"{row.get('source_ts')} {row.get('symbol')} {row.get('side')} "
            f"weight={row.get('weight')} price_ref={row.get('price_ref')} "
            f"mode={row.get('mode')} live_order=false"
        )
    if not rows and args.list:
        print("no pending paper orders")


if __name__ == "__main__":
    main()
