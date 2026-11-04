#!/data/data/com.termux/files/usr/bin/sh
set -eu

# Only accept a local archive authenticated by the installed auto-updater.
ARCHIVE="${1:?Supply a verified local archive path (not a URL)}"
EXPECTED_SHA="${2:?Supply SHA-256 from the HMAC-verified manifest}"
case "$ARCHIVE" in
  /*) ;;
  *) echo "refusing non-absolute archive path" >&2; exit 1 ;;
esac
[ -f "$ARCHIVE" ] || { echo "archive not found" >&2; exit 1; }

python - "$ARCHIVE" "$EXPECTED_SHA" <<'PY'
import hashlib
import hmac
import pathlib
import re
import sys
import tarfile

path = pathlib.Path(sys.argv[1])
expected = sys.argv[2].strip().lower()
if not re.fullmatch(r"[0-9a-f]{64}", expected):
    raise SystemExit("invalid expected SHA-256")
if path.stat().st_size > 40 * 1024 * 1024:
    raise SystemExit("archive too large")
sha = hashlib.sha256()
with path.open("rb") as source:
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        sha.update(chunk)
if not hmac.compare_digest(sha.hexdigest(), expected):
    raise SystemExit("archive checksum mismatch")
with tarfile.open(path, mode="r:gz") as archive:
    members = archive.getmembers()
    if len(members) > 300:
        raise SystemExit("too many archive members")
    total = 0
    for item in members:
        parts = item.name.rstrip("/").split("/")
        if (
            item.name.startswith("/")
            or parts[0] != "etf_mobile"
            or any(part in ("", ".", "..") for part in parts[1:])
            or (item.isfile() and len(parts) < 2)
            or not (item.isfile() or item.isdir())
            or item.mode & 0o6000
        ):
            raise SystemExit("unsafe archive member")
        total += item.size
        if total > 80 * 1024 * 1024:
            raise SystemExit("unpacked archive too large")
print("archive verified")
PY

tar -xzf "$ARCHIVE" -C "$HOME"

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
