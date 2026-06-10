#!/bin/zsh

set -u

cd /Users/grigorijtoropov/Desktop/polymarket || exit 1

echo "$$" > /Users/grigorijtoropov/Desktop/polymarket/.copy_trader_supervisor.pid
trap 'rm -f /Users/grigorijtoropov/Desktop/polymarket/.copy_trader_supervisor.pid >/dev/null 2>&1 || true' EXIT INT TERM

while true; do
  # Avoid restart storm if another instance is already holding the lock.
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 - <<'PY'
import fcntl, sys
path = "/Users/grigorijtoropov/Desktop/polymarket/.copy_trader.lock"
f = open(path, "a+")
try:
    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(1)
PY
  if [ $? -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [supervisor] lock busy, waiting" >> copy_trader.supervisor.log
    sleep 10
    continue
  fi

  echo "$(date '+%Y-%m-%d %H:%M:%S') [supervisor] starting copy_trader.py" >> copy_trader.supervisor.log
  /usr/bin/env \
    WATCH_ONLY_WALLET=0x04283f2fef49d70d8c55ab240450d17a65bf85b1 \
    SHORT_TERM_WALLET=0x04283f2fef49d70d8c55ab240450d17a65bf85b1 \
    COPY_ALL_SHORT_TERM_FOR_WALLET=0 \
    ALLOWED_MARKET_KEYWORDS=bitcoin,ethereum \
    COPY_NOTIONAL_USD=1.00 \
    MIN_ORDER_NOTIONAL=1.00 \
    MIN_ORDER_SIZE=5.50 \
    MIN_SELL_ORDER_SIZE=0.00 \
    SELL_ONLY_IN_PROFIT=1 \
    SHORT_TERM_MIN_PRICE=0.10 \
    SHORT_TERM_MAX_PRICE=0.30 \
    COPY_ORDER_TYPE=GTC \
    ORDER_COOLDOWN_SEC=30 \
    BALANCE_BUFFER_USD=0.00 \
    RESERVE_CASH_USD=5.00 \
    AUTO_SELL_AFTER_BUY_ENABLE=1 \
    AUTO_SELL_AFTER_BUY_PRICE=0.50 \
    AUTO_SELL_AFTER_BUY_DELAY_SEC=40 \
    PROFIT_TAKE_USD=30 \
    LOW_ENTRY_TAKE_PROFIT_ENABLE=1 \
    LOW_ENTRY_TAKE_PROFIT_USD=20 \
    LOW_ENTRY_MIN_AVG=0.10 \
    LOW_ENTRY_MAX_AVG=0.30 \
    ORDER_FILL_WAIT_SEC=90 \
    POLL_INTERVAL_SEC=10 \
    RECENT_TRADES_LIMIT=20 \
    BACKFILL_RECENT_TRADES=10 \
    FETCH_TIMEOUT_SEC=5 \
    FETCH_RETRIES=1 \
    HEARTBEAT_SEC=60 \
    /Users/grigorijtoropov/Desktop/polymarket/venv/bin/python -u /Users/grigorijtoropov/Desktop/polymarket/copy_trader.py \
    >> /Users/grigorijtoropov/Desktop/polymarket/copy_trader.log 2>&1
  code=$?
  echo "$(date '+%Y-%m-%d %H:%M:%S') [supervisor] copy_trader.py exited with code $code" >> copy_trader.supervisor.log
  if tail -n 20 /Users/grigorijtoropov/Desktop/polymarket/copy_trader.log 2>/dev/null | grep -q "copy_trader уже запущен"; then
    sleep 10
  else
    sleep 2
  fi
done
