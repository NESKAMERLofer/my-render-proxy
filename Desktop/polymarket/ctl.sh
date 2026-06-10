#!/bin/zsh
set -euo pipefail

ROOT="/Users/grigorijtoropov/Desktop/polymarket"
SUPERVISOR="$ROOT/run_copy_trader_forever.sh"
SUP_PID_FILE="$ROOT/.copy_trader_supervisor.pid"
SCREEN_SESSION="copy_trader_bot"

usage() {
  cat <<'TXT'
Usage:
  ./ctl.sh start    - start bot in detached screen session
  ./ctl.sh stop     - stop bot + detached screen session
  ./ctl.sh status   - show screen session, pids, lock, last heartbeat
  ./ctl.sh logs     - follow logs (bot + supervisor)
TXT
}

require_root() {
  cd "$ROOT"
}

is_running_pid() {
  local pid="$1"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1
}

read_pidfile() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  tr -d ' \n\t' < "$f"
}

lock_state() {
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 - <<'PY'
import fcntl, os
p="/Users/grigorijtoropov/Desktop/polymarket/.copy_trader.lock"
fd=os.open(p, os.O_RDWR|os.O_CREAT, 0o644)
try:
    fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
    print("LOCK_FREE")
except BlockingIOError:
    print("LOCK_BUSY")
PY
}

screen_session_running() {
  screen -ls | grep -q "[.]$SCREEN_SESSION"
}

cmd="${1:-}"
case "$cmd" in
  start)
    require_root
    # Clean up any old manual/launchd processes first so screen starts from a clean state.
    launchctl bootout "gui/$(id -u)/com.grigorijtoropov.polymarket.copytrader" >/dev/null 2>&1 || true
    pkill -TERM -f "run_copy_trader_forever" >/dev/null 2>&1 || true
    pkill -TERM -f "copy_trader.py" >/dev/null 2>&1 || true
    sleep 0.3
    pkill -KILL -f "run_copy_trader_forever" >/dev/null 2>&1 || true
    pkill -KILL -f "copy_trader.py" >/dev/null 2>&1 || true
    screen -S "$SCREEN_SESSION" -X quit >/dev/null 2>&1 || true

    screen -dmS "$SCREEN_SESSION" /bin/zsh -lc "cd '$ROOT' && exec '$SUPERVISOR'"
    sleep 1
    echo "Started screen session: $SCREEN_SESSION"
    ;;

  stop)
    require_root
    screen -S "$SCREEN_SESSION" -X quit >/dev/null 2>&1 || true
    launchctl bootout "gui/$(id -u)/com.grigorijtoropov.polymarket.copytrader" >/dev/null 2>&1 || true
    pkill -TERM -f "run_copy_trader_forever" >/dev/null 2>&1 || true
    pkill -TERM -f "copy_trader.py" >/dev/null 2>&1 || true
    sleep 0.2
    pkill -KILL -f "run_copy_trader_forever" >/dev/null 2>&1 || true
    pkill -KILL -f "copy_trader.py" >/dev/null 2>&1 || true

    rm -f "$SUP_PID_FILE" >/dev/null 2>&1 || true
    echo "Stopped."
    ;;

  status)
    require_root
    if screen_session_running; then
      echo "screen: running ($SCREEN_SESSION)"
      screen -ls | grep "$SCREEN_SESSION" || true
    else
      echo "screen: not running ($SCREEN_SESSION)"
    fi
    echo "lock: $(lock_state)"
    sup_pid="$(read_pidfile "$SUP_PID_FILE" 2>/dev/null || true)"
    if [[ -n "${sup_pid:-}" ]] && is_running_pid "$sup_pid"; then
      ps -p "$sup_pid" -o pid,etime,stat,command
    else
      echo "Supervisor: not running (pidfile=${sup_pid:-none})"
    fi

    bot_pid="$(pgrep -f "/Users/grigorijtoropov/Desktop/polymarket/copy_trader.py" | head -n 1 || true)"
    if [[ -z "${bot_pid:-}" ]]; then
      bot_pid="$(pgrep -f "copy_trader\\.py" | head -n 1 || true)"
    fi
    if [[ -n "${bot_pid:-}" ]]; then
      ps -p "$bot_pid" -o pid,etime,stat,command
    else
      echo "Bot: not running"
    fi

    if [[ -f "$ROOT/copy_trader.health.json" ]]; then
      echo "health:"
      cat "$ROOT/copy_trader.health.json"
    fi
    if [[ -f "$ROOT/copy_trader.log" ]]; then
      echo
      echo "last heartbeat:"
      tail -n 200 "$ROOT/copy_trader.log" | grep -a "💓 Heartbeat" | tail -n 1 || true
    fi
    ;;

  logs)
    require_root
    echo "Tailing logs. Ctrl+C to stop."
    # -F follows across truncation/rotation.
    tail -n 100 -F "$ROOT/copy_trader.supervisor.log" "$ROOT/copy_trader.log"
    ;;

  *)
    usage
    exit 1
    ;;
esac
