#!/data/data/com.termux/files/usr/bin/sh

set -u

BASE_URL="${1:-http://192.168.0.24:8765}"
INTERVAL_SEC="${2:-30}"
STATE_FILE="$HOME/.etf_mobile_pull.sha256"
LOG_DIR="$HOME/etf_mobile/logs"
LOG_FILE="$LOG_DIR/phone_auto_update.log"
TMP_DIR="${TMPDIR:-/data/data/com.termux/files/usr/tmp}"
UPDATE_SCRIPT="$TMP_DIR/phone_pull_update.sh"

mkdir -p "$LOG_DIR" "$TMP_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

log "phone auto update started base_url=$BASE_URL interval=${INTERVAL_SEC}s"

while true
do
  sha_line="$(curl -fsL "$BASE_URL/etf_mobile_pull.sha256" 2>/dev/null || true)"
  sha="$(printf '%s\n' "$sha_line" | awk '{print $1}')"
  old_sha=""
  if [ -f "$STATE_FILE" ]; then
    old_sha="$(cat "$STATE_FILE" 2>/dev/null || true)"
  fi

  if [ -n "$sha" ] && [ "$sha" != "$old_sha" ]; then
    log "update detected sha=$sha old=${old_sha:-none}"
    if curl -fsL -o "$UPDATE_SCRIPT" "$BASE_URL/phone_pull_update.sh" >> "$LOG_FILE" 2>&1; then
      if sh "$UPDATE_SCRIPT" "$BASE_URL/etf_mobile_pull.tar.gz" >> "$LOG_FILE" 2>&1; then
        printf '%s\n' "$sha" > "$STATE_FILE"
        log "update applied sha=$sha"
      else
        log "update failed while applying sha=$sha"
      fi
    else
      log "failed to download phone_pull_update.sh"
    fi
  fi

  sleep "$INTERVAL_SEC"
done
