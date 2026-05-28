#!/data/data/com.termux/files/usr/bin/sh

mkdir -p ~/etf_mobile/logs
cd ~/etf_mobile || exit 1

termux-wake-lock

APP_LOG=~/etf_mobile/logs/daytrade_app.log
RUNNER_LOG=~/etf_mobile/logs/daytrade_runner.log
FEED_LOG=~/etf_mobile/logs/daytrade_feed.log
SCREENER_LOG=~/etf_mobile/logs/daytrade_screener.log
WD_LOG=~/etf_mobile/logs/watchdog_daytrade.log
APP_HEALTH_URL="http://127.0.0.1:8010/health"
APP_LOG_MAX_BYTES=5242880
RUNNER_LOG_MAX_BYTES=5242880
FEED_LOG_MAX_BYTES=5242880
SCREENER_LOG_MAX_BYTES=5242880

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$WD_LOG"
}

log_prefixed_lines() {
  prefix="$1"
  while IFS= read -r line
  do
    log "$prefix$line"
  done
}

rotate_log_if_needed() {
  log_path="$1"
  log_name="$2"
  max_bytes="$3"

  if [ ! -f "$log_path" ]; then
    return
  fi

  size=$(wc -c < "$log_path" 2>/dev/null | tr -d ' ')
  case "$size" in
    ""|*[!0-9]*)
      log "$log_name log rotation skipped: size unknown"
      return
      ;;
  esac

  if [ "$size" -lt "$max_bytes" ]; then
    return
  fi

  if cp "$log_path" "$log_path.1" 2>/dev/null && : > "$log_path"; then
    log "$log_name log rotated: size=${size} backup=$log_path.1"
  else
    log "$log_name log rotation failed: size=${size}"
  fi
}

rotate_logs_if_needed() {
  rotate_log_if_needed "$APP_LOG" "daytrade_app" "$APP_LOG_MAX_BYTES"
  rotate_log_if_needed "$RUNNER_LOG" "daytrade_runner" "$RUNNER_LOG_MAX_BYTES"
  rotate_log_if_needed "$FEED_LOG" "daytrade_feed" "$FEED_LOG_MAX_BYTES"
  rotate_log_if_needed "$SCREENER_LOG" "daytrade_screener" "$SCREENER_LOG_MAX_BYTES"
}

log_app_diagnostics() {
  pids=$(pgrep -f "daytrade_app.py" 2>/dev/null | tr '\n' ' ')
  if [ -n "$pids" ]; then
    log "app diag: pids=$pids"
  else
    log "app diag: no daytrade app pid"
  fi

  if command -v ss >/dev/null 2>&1; then
    port_lines=$(ss -ltn 2>&1 | grep ':8010')
    if [ -n "$port_lines" ]; then
      printf '%s\n' "$port_lines" | log_prefixed_lines "app diag ss: "
    else
      log "app diag ss: no :8010 listener"
    fi
  elif command -v netstat >/dev/null 2>&1; then
    port_lines=$(netstat -ltn 2>&1 | grep ':8010')
    if [ -n "$port_lines" ]; then
      printf '%s\n' "$port_lines" | log_prefixed_lines "app diag netstat: "
    else
      log "app diag netstat: no :8010 listener"
    fi
  else
    log "app diag: ss/netstat unavailable"
  fi

  if [ -f "$APP_LOG" ]; then
    tail -n 20 "$APP_LOG" 2>&1 | log_prefixed_lines "daytrade_app.log tail: "
  else
    log "app diag: daytrade app log missing"
  fi
}

start_app() {
  pkill -f daytrade_app.py
  sleep 1
  rotate_logs_if_needed
  log "daytrade app restart"
  DAYTRADE_PORT=8010 nohup python -u daytrade_app.py >> "$APP_LOG" 2>&1 &
}

start_runner() {
  pkill -f daytrade_runner.py
  sleep 1
  rotate_logs_if_needed
  log "daytrade runner restart"
  DAYTRADE_CHECK_INTERVAL_SEC="${DAYTRADE_CHECK_INTERVAL_SEC:-1}" nohup python -u daytrade_runner.py >> "$RUNNER_LOG" 2>&1 &
}

start_feed() {
  pkill -f daytrade_demo_feed.py
  sleep 1
  rotate_logs_if_needed
  log "daytrade demo feed restart"
  nohup python -u daytrade_demo_feed.py --loop --realtime --run-runner --interval-minutes "${DAYTRADE_FEED_INTERVAL_MINUTES:-1}" --sleep "${DAYTRADE_FEED_SLEEP_SEC:-1}" >> "$FEED_LOG" 2>&1 &
}

start_screener() {
  pkill -f "daytrade_screener.py --loop"
  sleep 1
  rotate_logs_if_needed
  log "daytrade screener restart"
  nohup python -u daytrade_screener.py --loop --sleep "${DAYTRADE_SCREENER_SLEEP_SEC:-20}" >> "$SCREENER_LOG" 2>&1 &
}

demo_feed_enabled() {
  case "${DAYTRADE_DEMO_FEED:-auto}" in
    1|true|TRUE|yes|YES|on|ON)
      return 0
      ;;
    0|false|FALSE|no|NO|off|OFF)
      return 1
      ;;
  esac

  python - <<'PY'
import json
import sys
from pathlib import Path

path = Path.home() / "etf_mobile" / "data_daytrade" / "runtime_config.json"
try:
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
except Exception:
    cfg = {}
adapter = str(cfg.get("market_data_adapter", "csv")).strip().lower()
sys.exit(0 if adapter in ("", "csv", "demo_csv") else 1)
PY
}

app_health_ok() {
  APP_HEALTH_URL="$APP_HEALTH_URL" python - <<'PY'
import os
import sys
import urllib.request

url = os.environ.get("APP_HEALTH_URL", "http://127.0.0.1:8010/")
try:
    r = urllib.request.urlopen(url, timeout=5)
    print(f"status={r.status} url={url}")
    sys.exit(0 if r.status == 200 else 1)
except Exception as e:
    print(f"error={type(e).__name__}: {e} url={url}")
    sys.exit(1)
PY
}

runner_health_ok() {
  python - <<'PY'
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

root = Path.home() / "etf_mobile"
data_dir = root / "data_daytrade"
symbols_path = data_dir / "symbols.json"

def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

try:
    rows = load_json(symbols_path)
    symbols = [
        str(row.get("symbol", "")).strip()
        for row in rows
        if row.get("enabled", True) is not False and str(row.get("symbol", "")).strip()
    ]
except Exception:
    symbols = []

if not symbols:
    print("no enabled daytrade symbols")
    sys.exit(1)

failures = []
for symbol in symbols:
    p = data_dir / symbol / "paper_state_daytrade.json"
    try:
        d = load_json(p)
        s = str(d.get("last_runner_at", "")).strip()
        if not s:
            failures.append(f"{symbol}: last_runner_at missing")
            continue
        t = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        age = datetime.now() - t
        if age > timedelta(seconds=90):
            failures.append(f"{symbol}: stale last_runner_at={s} age_sec={int(age.total_seconds())}")
    except Exception as e:
        failures.append(f"{symbol}: {type(e).__name__}: {e}")

if failures:
    print("; ".join(failures))
    sys.exit(1)

print(f"ok symbols={','.join(symbols)}")
sys.exit(0)
PY
}

log "daytrade watchdog started"

start_app
sleep 5
start_runner
sleep 2
start_screener
sleep 1
if demo_feed_enabled; then
  start_feed
  sleep 1
fi

while true
do
  rotate_logs_if_needed

  if ! pgrep -f "daytrade_app.py" >/dev/null 2>&1; then
    log "daytrade app process missing"
    start_app
    sleep 8
  else
    app_health_result=$(app_health_ok 2>&1)
    app_health_status=$?
    if [ "$app_health_status" -ne 0 ]; then
      log "daytrade app health failed: $app_health_result"
      log_app_diagnostics
      start_app
      sleep 8
    fi
  fi

  rotate_logs_if_needed

  if ! pgrep -f "daytrade_runner.py" >/dev/null 2>&1; then
    log "daytrade runner process missing"
    start_runner
    sleep 5
  else
    runner_health_result=$(runner_health_ok 2>&1)
    runner_health_status=$?
    if [ "$runner_health_status" -ne 0 ]; then
      log "daytrade runner health failed: $runner_health_result"
      start_runner
      sleep 5
    fi
  fi

  if demo_feed_enabled; then
    if ! pgrep -f "daytrade_demo_feed.py" >/dev/null 2>&1; then
      log "daytrade demo feed process missing"
      start_feed
      sleep 3
    fi
  fi

  if ! pgrep -f "daytrade_screener.py --loop" >/dev/null 2>&1; then
    log "daytrade screener process missing"
    start_screener
    sleep 3
  fi

  sleep 20
done
