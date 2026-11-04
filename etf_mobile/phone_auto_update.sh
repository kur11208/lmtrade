#!/data/data/com.termux/files/usr/bin/sh
set -eu
umask 077

BASE_URL="${1:-http://192.168.0.24:8765}"
INTERVAL_SEC="${2:-30}"
STATE_FILE="$HOME/.etf_mobile_pull.sha256"
KEY_FILE="$HOME/.config/etf_mobile/update_hmac.key"
LOG_DIR="$HOME/etf_mobile/logs"
LOG_FILE="$LOG_DIR/phone_auto_update.log"
TMP_DIR="${TMPDIR:-/data/data/com.termux/files/usr/tmp}"
MANIFEST="$TMP_DIR/etf_mobile_pull.sha256"
SIGNATURE="$TMP_DIR/etf_mobile_pull.sha256.hmac"
ARCHIVE="$TMP_DIR/etf_mobile_pull.tar.gz"
LOCAL_UPDATER="$TMP_DIR/etf_mobile_trusted_pull_update.sh"

mkdir -p "$LOG_DIR" "$TMP_DIR"
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}
case "$INTERVAL_SEC" in
  *[!0-9]*|"") echo "invalid interval" >&2; exit 1 ;;
esac
if [ "$INTERVAL_SEC" -lt 30 ]; then INTERVAL_SEC=30; fi
if [ ! -r "$KEY_FILE" ]; then
  log "auto-update disabled: private HMAC key is missing; use trusted SSH deployment"
  echo "Configure $KEY_FILE (chmod 600) before enabling auto-update." >&2
  exit 1
fi
if [ ! -r "$HOME/etf_mobile/phone_pull_update.sh" ]; then
  log "auto-update disabled: trusted local pull script is missing"
  exit 1
fi

# A compromised LAN server cannot authorize an update without the shared key.
verify_manifest() {
  python - "$MANIFEST" "$SIGNATURE" "$KEY_FILE" <<'PY'
import hashlib
import hmac
import pathlib
import re
import sys

raw = pathlib.Path(sys.argv[1]).read_bytes()
signature = pathlib.Path(sys.argv[2]).read_text(encoding="ascii").strip().lower()
key = pathlib.Path(sys.argv[3]).read_bytes().strip()
if len(key) < 32 or not re.fullmatch(r"[a-f0-9]{64}", signature):
    raise SystemExit(1)
expected = hmac.new(key, raw, hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, signature):
    raise SystemExit(1)
match = re.fullmatch(r"([a-f0-9]{64})  etf_mobile_pull\.tar\.gz\r?\n?", raw.decode("ascii"))
if not match:
    raise SystemExit(1)
print(match.group(1))
PY
}

log "authenticated phone auto-update started base_url=$BASE_URL interval=${INTERVAL_SEC}s"
while true
do
  if ! curl -fsSL --max-time 15 -o "$MANIFEST" "$BASE_URL/etf_mobile_pull.sha256" ||
     ! curl -fsSL --max-time 15 -o "$SIGNATURE" "$BASE_URL/etf_mobile_pull.sha256.hmac"; then
    log "manifest fetch failed"
    sleep "$INTERVAL_SEC"
    continue
  fi

  if sha="$(verify_manifest 2>/dev/null)"; then
    old_sha=""
    if [ -r "$STATE_FILE" ]; then old_sha="$(cat "$STATE_FILE")"; fi
    if [ "$sha" != "$old_sha" ]; then
      log "authenticated update available"
      if curl -fsSL --max-time 90 -o "$ARCHIVE" "$BASE_URL/etf_mobile_pull.tar.gz" &&
         cp "$HOME/etf_mobile/phone_pull_update.sh" "$LOCAL_UPDATER" &&
         sh "$LOCAL_UPDATER" "$ARCHIVE" "$sha" >> "$LOG_FILE" 2>&1; then
        printf '%s\n' "$sha" > "$STATE_FILE"
        log "authenticated update installed sha=$sha"
      else
        log "update failed (download, validation or installation)"
      fi
    fi
  else
    log "invalid signed manifest: update refused"
  fi
  sleep "$INTERVAL_SEC"
done
