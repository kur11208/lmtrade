#!/data/data/com.termux/files/usr/bin/sh

mkdir -p ~/etf_mobile/logs
cd ~/etf_mobile || exit 1

termux-wake-lock

APP_LOG=~/etf_mobile/logs/app.log
RUNNER_LOG=~/etf_mobile/logs/runner.log
WD_LOG=~/etf_mobile/logs/watchdog.log
APP_HEALTH_URL="http://127.0.0.1:8000/"
APP_LOG_MAX_BYTES=5242880
RUNNER_LOG_MAX_BYTES=5242880
OHLC_REFRESH_MIN_INTERVAL_SEC="${LONG_ETF_OHLC_REFRESH_MIN_INTERVAL_SEC:-1800}"
LAST_OHLC_REFRESH_TS=0

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

rotate_app_log_if_needed() {
  rotate_log_if_needed "$APP_LOG" "app" "$APP_LOG_MAX_BYTES"
}

rotate_runner_log_if_needed() {
  rotate_log_if_needed "$RUNNER_LOG" "runner" "$RUNNER_LOG_MAX_BYTES"
}

rotate_logs_if_needed() {
  rotate_app_log_if_needed
  rotate_runner_log_if_needed
}

log_app_diagnostics() {
  pids=$(pgrep -f "app_pc_multi.py" 2>/dev/null | tr '\n' ' ')
  if [ -n "$pids" ]; then
    log "app diag: pids=$pids"
  else
    log "app diag: no app pid"
  fi

  if command -v ss >/dev/null 2>&1; then
    port_lines=$(ss -ltn 2>&1 | grep ':8000')
    if [ -n "$port_lines" ]; then
      printf '%s\n' "$port_lines" | log_prefixed_lines "app diag ss: "
    else
      log "app diag ss: no :8000 listener"
    fi
  elif command -v netstat >/dev/null 2>&1; then
    port_lines=$(netstat -ltn 2>&1 | grep ':8000')
    if [ -n "$port_lines" ]; then
      printf '%s\n' "$port_lines" | log_prefixed_lines "app diag netstat: "
    else
      log "app diag netstat: no :8000 listener"
    fi
  else
    log "app diag: ss/netstat unavailable"
  fi

  if [ -f "$APP_LOG" ]; then
    tail -n 20 "$APP_LOG" 2>&1 | log_prefixed_lines "app.log tail: "
  else
    log "app diag: app log missing"
  fi
}

start_app() {
  pkill -f app_pc_multi.py
  sleep 1
  rotate_app_log_if_needed
  log "app restart"
  nohup python -u app_pc_multi.py >> "$APP_LOG" 2>&1 &
}

start_runner() {
  pkill -f runner_pc_multi.py
  sleep 1
  rotate_runner_log_if_needed
  log "runner restart"
  nohup python -u runner_pc_multi.py >> "$RUNNER_LOG" 2>&1 &
}

maybe_refresh_ohlc() {
  case "${LONG_ETF_AUTO_FETCH_OHLC:-1}" in
    0|false|FALSE|no|NO|off|OFF)
      log "ohlc refresh skipped: LONG_ETF_AUTO_FETCH_OHLC disabled"
      return
      ;;
  esac

  now_ts=$(date +%s)
  elapsed=$((now_ts - LAST_OHLC_REFRESH_TS))
  if [ "$LAST_OHLC_REFRESH_TS" -ne 0 ] && [ "$elapsed" -lt "$OHLC_REFRESH_MIN_INTERVAL_SEC" ]; then
    log "ohlc refresh skipped: throttled elapsed=${elapsed}s"
    return
  fi

  LAST_OHLC_REFRESH_TS="$now_ts"
  rotate_runner_log_if_needed
  log "ohlc refresh start: $1"
  python -u fetch_candidate_ohlc.py --refresh --update-symbols >> "$RUNNER_LOG" 2>&1
  status=$?
  log "ohlc refresh finished: status=$status"
  if [ "$status" -eq 0 ]; then
    python -u runner_pc_multi.py --signal-now >> "$RUNNER_LOG" 2>&1 || log "signal-now after ohlc refresh failed"
  fi
}

app_health_ok() {
  APP_HEALTH_URL="$APP_HEALTH_URL" python - <<'PY'
import os
import sys, urllib.request
url = os.environ.get("APP_HEALTH_URL", "http://127.0.0.1:8000/")
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
import json, sys
from datetime import datetime, timedelta
from pathlib import Path
import runner_pc_multi as runner

root = Path.home() / "etf_mobile"
data_dir = root / "data"
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
    symbols = ["1321", "1306", "1343", "1540", "2510"]

if not symbols:
    print("no enabled symbols")
    sys.exit(1)

failures = []
expected_date = runner.expected_market_data_date(datetime.now())
for symbol in symbols:
    p = data_dir / symbol / "paper_state_atr.json"
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
        latest = str(d.get("data_latest_ohlc_date") or d.get("latest_date") or "").strip()
        status = str(d.get("data_freshness_status", "")).strip()
        source = str(d.get("price_source", "")).strip()
        if not latest:
            failures.append(f"{symbol}: latest_date missing expected={expected_date}")
        elif latest < expected_date:
            failures.append(f"{symbol}: stale latest_date={latest} expected={expected_date}")
        if status in ("missing_ohlc", "stale_ohlc") or source in ("missing_ohlc", "stale_ohlc"):
            failures.append(f"{symbol}: data blocked status={status} source={source}")
    except Exception as e:
        failures.append(f"{symbol}: {type(e).__name__}: {e}")

if failures:
    print("; ".join(failures))
    sys.exit(1)

print(f"ok symbols={','.join(symbols)}")
sys.exit(0)
PY
}

log "watchdog started"

start_app
sleep 8
maybe_refresh_ohlc "startup"
start_runner
sleep 2

while true
do
  rotate_logs_if_needed

  if ! pgrep -f "app_pc_multi.py" >/dev/null 2>&1; then
    log "app process missing"
    start_app
    sleep 10
  else
    app_health_result=$(app_health_ok 2>&1)
    app_health_status=$?
    if [ "$app_health_status" -ne 0 ]; then
      log "app health failed: $app_health_result"
      log_app_diagnostics
      start_app
      sleep 10
    fi
  fi

  rotate_logs_if_needed

  if ! pgrep -f "runner_pc_multi.py" >/dev/null 2>&1; then
    log "runner process missing"
    start_runner
    sleep 5
  else
    runner_health_result=$(runner_health_ok 2>&1)
    runner_health_status=$?
    if [ "$runner_health_status" -ne 0 ]; then
      log "runner health failed: $runner_health_result"
      maybe_refresh_ohlc "runner health failed"
      start_runner
      sleep 5
    fi
  fi

  sleep 20
done
