#!/data/data/com.termux/files/usr/bin/sh

mkdir -p ~/etf_mobile/logs
cd ~/etf_mobile || exit 1

termux-wake-lock

LOG=~/etf_mobile/logs/supervisor.log

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
}

start_watchdog() {
  pkill -f "watchdog_etf.sh"
  sleep 1
  log "watchdog restart"
  nohup sh ~/etf_mobile/watchdog_etf.sh >> ~/etf_mobile/logs/watchdog_launcher.log 2>&1 &
}

start_daytrade_watchdog() {
  pkill -f "watchdog_daytrade.sh"
  sleep 1
  log "daytrade watchdog restart"
  nohup sh ~/etf_mobile/watchdog_daytrade.sh >> ~/etf_mobile/logs/watchdog_daytrade_launcher.log 2>&1 &
}

start_pc_wake_watchdog() {
  pkill -f "phone_wake_pc.sh"
  sleep 1
  log "pc wake watchdog restart"
  nohup sh ~/etf_mobile/phone_wake_pc.sh >> ~/etf_mobile/logs/phone_wake_pc_launcher.log 2>&1 &
}

log "supervisor started"

while true
do
  if ! pgrep -f "watchdog_etf.sh" >/dev/null 2>&1; then
    log "watchdog missing"
    start_watchdog
    sleep 5
  fi

  if ! pgrep -f "watchdog_daytrade.sh" >/dev/null 2>&1; then
    log "daytrade watchdog missing"
    start_daytrade_watchdog
    sleep 5
  fi

  if [ -f ~/etf_mobile/phone_wake_pc.sh ] && ! pgrep -f "phone_wake_pc.sh" >/dev/null 2>&1; then
    log "pc wake watchdog missing"
    start_pc_wake_watchdog
    sleep 5
  fi

  sleep 20
done
