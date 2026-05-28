#!/data/data/com.termux/files/usr/bin/sh

set -eu

ARCHIVE_URL="${1:-http://192.168.0.24:8765/etf_mobile_pull.tar.gz}"
TMP_ARCHIVE="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/etf_mobile_pull.tar.gz"

echo "archive_url=$ARCHIVE_URL"
mkdir -p "${TMPDIR:-/data/data/com.termux/files/usr/tmp}"

curl -fL -o "$TMP_ARCHIVE" "$ARCHIVE_URL"
tar -xzf "$TMP_ARCHIVE" -C "$HOME"

cd "$HOME/etf_mobile"
mkdir -p logs
python -m py_compile \
  app_pc_multi.py \
  runner_pc_multi.py \
  set_demo_mode.py \
  daytrade_app.py \
  daytrade_runner.py \
  daytrade_market_calendar.py \
  daytrade_market_data.py \
  daytrade_status.py \
  daytrade_view_model.py \
  daytrade_config.py \
  daytrade_order_queue.py \
  daytrade_order_paper.py \
  daytrade_feed_api_stub.py \
  daytrade_screener.py \
  daytrade_backtest.py \
  daytrade_demo_feed.py \
  daytrade_import_csv.py \
  daytrade_sqlite.py \
  daytrade_alerts.py

python runner_pc_multi.py --backfill-history

pkill -f '[w]atchdog_supervisor.sh' 2>/dev/null || true
pkill -f '[w]atchdog_etf.sh' 2>/dev/null || true
pkill -f '[w]atchdog_daytrade.sh' 2>/dev/null || true
pkill -f '[a]pp_pc_multi.py' 2>/dev/null || true
pkill -f '[r]unner_pc_multi.py' 2>/dev/null || true
pkill -f '[d]aytrade_app.py' 2>/dev/null || true
pkill -f '[d]aytrade_runner.py' 2>/dev/null || true
pkill -f '[d]aytrade_demo_feed.py' 2>/dev/null || true
pkill -f '[d]aytrade_screener.py --loop' 2>/dev/null || true

nohup sh watchdog_supervisor.sh >> logs/supervisor_launcher.log 2>&1 &
sleep 5

wait_http() {
  name="$1"
  url="$2"
  attempts="$3"
  n=1
  while [ "$n" -le "$attempts" ]
  do
    code="$(curl -sS -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)"
    if [ "$code" = "200" ]; then
      echo "${name}_http=200"
      return 0
    fi
    sleep 2
    n=$((n + 1))
  done
  echo "${name}_http=${code:-000}"
  return 0
}

wait_http etf http://127.0.0.1:8000/ 15
wait_http daytrade http://127.0.0.1:8010/ 15
echo "done"
