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
python -m py_compile app_pc_multi.py runner_pc_multi.py set_demo_mode.py

pkill -f '[w]atchdog_supervisor.sh' 2>/dev/null || true
pkill -f '[w]atchdog_etf.sh' 2>/dev/null || true
pkill -f '[a]pp_pc_multi.py' 2>/dev/null || true
pkill -f '[r]unner_pc_multi.py' 2>/dev/null || true

nohup sh watchdog_supervisor.sh >> logs/supervisor_launcher.log 2>&1 &
sleep 5

grep -q "資産推移と現在状態" app_pc_multi.py && echo "updated_file=1"
curl -sS -o /dev/null -w 'http=%{http_code}\n' http://127.0.0.1:8000/
echo "done"
