#!/data/data/com.termux/files/usr/bin/sh

set -u

TARGET_MAC="${LMTRADE_PC_MAC:-}"
BROADCAST="${LMTRADE_PC_BROADCAST:-192.168.0.255}"
PORT="${LMTRADE_PC_WOL_PORT:-9}"
WAKE_TIME="${LMTRADE_PC_WAKE_TIME:-08:45}"
WAKE_WINDOW_MINUTES="${LMTRADE_PC_WAKE_WINDOW_MINUTES:-20}"
INTERVAL_SEC="${LMTRADE_PC_WAKE_CHECK_SECONDS:-30}"
STATE_FILE="$HOME/.lmtrade_pc_wake_date"
LOG_DIR="$HOME/etf_mobile/logs"
LOG_FILE="$LOG_DIR/phone_wake_pc.log"
WAKE_JSON="$HOME/etf_mobile/data_daytrade/pc_wake_state.json"

mkdir -p "$LOG_DIR" "$(dirname "$WAKE_JSON")"
if [ -z "$TARGET_MAC" ]; then
  log "LMTRADE_PC_MAC is not set; wake skipped"
  echo "LMTRADE_PC_MAC is not set. Set it before running phone_wake_pc.sh."
  exit 1
fi
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

is_trading_day() {
  cd "$HOME/etf_mobile" 2>/dev/null || return 1
  python -c "import datetime as d; import daytrade_market_calendar as c; print('1' if c.is_trading_day(d.date.today()) else '0')" 2>/dev/null || \
    python -c "import datetime as d; print('1' if d.date.today().weekday() < 5 else '0')" 2>/dev/null || \
    echo "1"
}

now_minutes() {
  date '+%H %M' | awk '{print ($1 * 60) + $2}'
}

wake_minutes() {
  printf '%s\n' "$WAKE_TIME" | awk -F: '{print ($1 * 60) + $2}'
}

send_wol() {
  python - "$TARGET_MAC" "$BROADCAST" "$PORT" <<'PY'
import socket
import sys

mac = sys.argv[1].replace("-", "").replace(":", "").strip()
broadcast = sys.argv[2]
port = int(sys.argv[3])
if len(mac) != 12:
    raise SystemExit(f"invalid mac: {sys.argv[1]}")
payload = b"\xff" * 6 + bytes.fromhex(mac) * 16
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for _ in range(5):
    sock.sendto(payload, (broadcast, port))
PY
}

write_wake_state() {
  now="$(date '+%Y-%m-%d %H:%M:%S')"
  today="$(date '+%Y-%m-%d')"
  cat > "$WAKE_JSON" <<EOF
{"ok":true,"date":"$today","wake_sent_at":"$now","mac":"$TARGET_MAC","broadcast":"$BROADCAST","port":$PORT}
EOF
}

log "phone wake pc started mac=$TARGET_MAC broadcast=$BROADCAST wake_time=$WAKE_TIME"

while true
do
  today="$(date '+%Y-%m-%d')"
  sent_today=""
  if [ -f "$STATE_FILE" ]; then
    sent_today="$(cat "$STATE_FILE" 2>/dev/null || true)"
  fi

  start_min="$(wake_minutes)"
  current_min="$(now_minutes)"
  end_min=$((start_min + WAKE_WINDOW_MINUTES))

  if [ "$sent_today" != "$today" ] && [ "$current_min" -ge "$start_min" ] && [ "$current_min" -le "$end_min" ]; then
    if [ "$(is_trading_day)" = "1" ]; then
      if send_wol >> "$LOG_FILE" 2>&1; then
        write_wake_state
        printf '%s\n' "$today" > "$STATE_FILE"
        log "wake packet sent"
      else
        log "wake packet failed"
      fi
    else
      printf '%s\n' "$today" > "$STATE_FILE"
      log "trading holiday; wake skipped"
    fi
  fi

  sleep "$INTERVAL_SEC"
done
