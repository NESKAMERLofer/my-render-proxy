"""
Copy trader — напрямую мониторит кошельки через Polymarket API
и копирует их сделки через CLOB.
"""

import asyncio
import atexit
import fcntl
import logging
import os
import json
import math
import re
import time
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass

import requests
from py_clob_client_v2 import ClobClient, ApiCreds, BalanceAllowanceParams, AssetType, OpenOrderParams, OrderArgs, OrderType
from py_clob_client_v2.constants import POLYGON

import config
import database

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

# ─── настройки ────────────────────────────────────────────────────────────────

POLY_KEY     = os.getenv("POLY_PRIVATE_KEY", "")
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
POLY_SECRET  = os.getenv("POLY_SECRET", "")
POLY_PASS    = os.getenv("POLY_PASSPHRASE", "")
POLY_FUNDER  = os.getenv("POLY_FUNDER_ADDRESS", "")
SIGNATURE_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "0"))

COPY_NOTIONAL_USD = float(os.getenv("COPY_NOTIONAL_USD", "1.05"))
COPY_TRADE_FRACTION = float(os.getenv("COPY_TRADE_FRACTION", "0.03"))
COPY_MODE = os.getenv("COPY_MODE", "fraction").strip().lower()
BALANCE_RISK_PCT = float(os.getenv("BALANCE_RISK_PCT", "0"))
FIXED_ORDER_SIZE = float(os.getenv("FIXED_ORDER_SIZE", "0"))
SOURCE_SIZE_MIN = float(os.getenv("SOURCE_SIZE_MIN", "0"))
SOURCE_SIZE_MAX = float(os.getenv("SOURCE_SIZE_MAX", "0"))
MIN_ORDER_SIZE = float(os.getenv("MIN_ORDER_SIZE", "5.5"))
MIN_SELL_ORDER_SIZE = float(os.getenv("MIN_SELL_ORDER_SIZE", "0"))
MIN_ORDER_NOTIONAL = float(os.getenv("MIN_ORDER_NOTIONAL", "1"))
EXCHANGE_MIN_ORDER_SIZE = 5.0
# Keep a safety buffer so we don't try to spend the last cents and hit "balance reserved" errors.
BALANCE_BUFFER_USD = float(os.getenv("BALANCE_BUFFER_USD", "0.50"))
# Keep a hard reserve in USDC.e for emergencies (bot won't spend below this).
RESERVE_CASH_USD = float(os.getenv("RESERVE_CASH_USD", "0.0"))
COPY_SELLS = os.getenv("COPY_SELLS", "1").lower() in {"1", "true", "yes", "on"}
SELL_ONLY_IN_PROFIT = os.getenv("SELL_ONLY_IN_PROFIT", "1").lower() in {"1", "true", "yes", "on"}
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "30"))   # опрос каждые 30 сек
ORDER_COOLDOWN_SEC = int(os.getenv("ORDER_COOLDOWN_SEC", "180"))
ORDER_FILL_WAIT_SEC = int(os.getenv("ORDER_FILL_WAIT_SEC", "90"))
COPY_ORDER_TYPE = os.getenv("COPY_ORDER_TYPE", "GTC").upper()
SHORT_TERM_WALLET = os.getenv("SHORT_TERM_WALLET", "").lower()
SHORT_TERM_MIN_PRICE = float(os.getenv("SHORT_TERM_MIN_PRICE", "0.05"))
SHORT_TERM_MAX_PRICE = float(os.getenv("SHORT_TERM_MAX_PRICE", "0.20"))
COPY_ALL_SHORT_TERM_FOR_WALLET = os.getenv("COPY_ALL_SHORT_TERM_FOR_WALLET", "0").lower() in {"1", "true", "yes", "on"}
ALLOWED_MARKET_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv("ALLOWED_MARKET_KEYWORDS", "").split(",")
    if x.strip()
]
RECENT_TRADES_LIMIT = int(os.getenv("RECENT_TRADES_LIMIT", "5"))
BACKFILL_RECENT_TRADES = int(os.getenv("BACKFILL_RECENT_TRADES", "0"))
FETCH_TIMEOUT_SEC = int(os.getenv("FETCH_TIMEOUT_SEC", "5"))
FETCH_RETRIES = int(os.getenv("FETCH_RETRIES", "1"))
WATCH_ONLY_WALLET = os.getenv("WATCH_ONLY_WALLET", "").lower()
HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_SEC", "60"))
SEEN_TRADES_FILE = os.getenv("SEEN_TRADES_FILE", os.path.join(os.path.dirname(__file__), "seen_trade_ids.json"))
SEEN_TRADES_MAX = int(os.getenv("SEEN_TRADES_MAX", "2000"))
LOCK_FILE = os.getenv("COPY_TRADER_LOCK_FILE", os.path.join(os.path.dirname(__file__), ".copy_trader.lock"))
HEALTH_FILE = os.getenv("COPY_TRADER_HEALTH_FILE", os.path.join(os.path.dirname(__file__), "copy_trader.health.json"))
HTTP_BACKOFF_SEC = float(os.getenv("HTTP_BACKOFF_SEC", "0.75"))
# Короткий кэш позиций, чтобы не дёргать data-api для каждой проверки профита
# (find_my_position вызывается по разу на позицию внутри одного прохода).
POSITIONS_CACHE_TTL_SEC = float(os.getenv("POSITIONS_CACHE_TTL_SEC", "3"))

# Take-profit (lock profit) logic:
# When a position goes +20% (or more) vs avg entry, place a SELL limit at +10%.
TAKE_PROFIT_ENABLE = os.getenv("TAKE_PROFIT_ENABLE", "1").lower() in {"1", "true", "yes", "on"}
TAKE_PROFIT_TRIGGER_PCT = float(os.getenv("TAKE_PROFIT_TRIGGER_PCT", "0.20"))
TAKE_PROFIT_LOCK_PCT = float(os.getenv("TAKE_PROFIT_LOCK_PCT", "0.10"))
TAKE_PROFIT_CHECK_SEC = int(os.getenv("TAKE_PROFIT_CHECK_SEC", "20"))
TAKE_PROFIT_MAX_PER_CHECK = int(os.getenv("TAKE_PROFIT_MAX_PER_CHECK", "2"))
TAKE_PROFIT_DRY_RUN = os.getenv("TAKE_PROFIT_DRY_RUN", "0").lower() in {"1", "true", "yes", "on"}
AUTO_SELL_AFTER_BUY_ENABLE = os.getenv("AUTO_SELL_AFTER_BUY_ENABLE", "0").lower() in {"1", "true", "yes", "on"}
AUTO_SELL_AFTER_BUY_PRICE = float(os.getenv("AUTO_SELL_AFTER_BUY_PRICE", "0.50"))
AUTO_SELL_AFTER_BUY_DELAY_SEC = int(os.getenv("AUTO_SELL_AFTER_BUY_DELAY_SEC", "3"))
TAKE_PROFIT_STATE_FILE = os.getenv(
    "TAKE_PROFIT_STATE_FILE",
    os.path.join(os.path.dirname(__file__), "take_profit_state.json"),
)

# Immediate profit-take logic:
# If a position profit (cashPnl / mark-to-market) is >= threshold USD, close it immediately.
PROFIT_TAKE_ENABLE = os.getenv("PROFIT_TAKE_ENABLE", "1").lower() in {"1", "true", "yes", "on"}
PROFIT_TAKE_USD = float(os.getenv("PROFIT_TAKE_USD", "30"))
PROFIT_TAKE_CHECK_SEC = int(os.getenv("PROFIT_TAKE_CHECK_SEC", "10"))
PROFIT_TAKE_MAX_PER_CHECK = int(os.getenv("PROFIT_TAKE_MAX_PER_CHECK", "1"))
PROFIT_TAKE_DRY_RUN = os.getenv("PROFIT_TAKE_DRY_RUN", "0").lower() in {"1", "true", "yes", "on"}

# Low-entry instant take-profit:
# If avg entry is in [min,max] and profit hits threshold USD, close immediately.
LOW_ENTRY_TAKE_PROFIT_ENABLE = os.getenv("LOW_ENTRY_TAKE_PROFIT_ENABLE", "1").lower() in {"1", "true", "yes", "on"}
LOW_ENTRY_TAKE_PROFIT_USD = float(os.getenv("LOW_ENTRY_TAKE_PROFIT_USD", "20"))
LOW_ENTRY_MIN_AVG = float(os.getenv("LOW_ENTRY_MIN_AVG", "0.10"))
LOW_ENTRY_MAX_AVG = float(os.getenv("LOW_ENTRY_MAX_AVG", "0.30"))

# Уже обработанные трейды (чтобы не копировать дважды)
seen_trade_ids: set[str] = set()
seen_trade_order: list[str] = []
recent_order_times: dict[tuple[str, str], float] = {}
background_tasks: set[asyncio.Task] = set()
lock_handle = None
http_session = requests.Session()
take_profit_state: dict[str, dict] = {}
_positions_cache: list[dict] = []
_positions_cache_at: float = 0.0


@dataclass
class WalletRule:
    address: str
    copy_mode: str
    balance_risk_pct: float
    fixed_order_size: float
    source_size_min: float
    source_size_max: float


def acquire_single_instance_lock() -> None:
    global lock_handle
    lock_handle = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.seek(0)
        existing_pid = (lock_handle.read() or "").strip()
        raise RuntimeError(f"copy_trader уже запущен (pid: {existing_pid or '?'}, lock: {LOCK_FILE})")
    lock_handle.seek(0)
    lock_handle.truncate()
    lock_handle.write(str(os.getpid()))
    lock_handle.flush()


def release_single_instance_lock() -> None:
    global lock_handle
    if lock_handle is None:
        return
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        lock_handle.close()
    except Exception:
        pass
    lock_handle = None


def load_seen_trade_ids() -> None:
    global seen_trade_order
    if not os.path.exists(SEEN_TRADES_FILE):
        return
    try:
        data = json.load(open(SEEN_TRADES_FILE))
        if not isinstance(data, list):
            return
        normalized = [str(item) for item in data if item]
        seen_trade_order = normalized[-SEEN_TRADES_MAX:]
        seen_trade_ids.update(seen_trade_order)
        logger.info("Загружено %d seen_trade_ids с диска", len(seen_trade_order))
    except Exception:
        logger.exception("Не смог загрузить seen_trade_ids из %s", SEEN_TRADES_FILE)


def save_seen_trade_ids() -> None:
    try:
        with open(SEEN_TRADES_FILE, "w") as f:
            json.dump(seen_trade_order[-SEEN_TRADES_MAX:], f)
    except Exception:
        logger.exception("Не смог сохранить seen_trade_ids в %s", SEEN_TRADES_FILE)


def load_take_profit_state() -> None:
    global take_profit_state
    if not os.path.exists(TAKE_PROFIT_STATE_FILE):
        take_profit_state = {}
        return
    try:
        data = json.load(open(TAKE_PROFIT_STATE_FILE))
        if isinstance(data, dict):
            take_profit_state = data
        else:
            take_profit_state = {}
    except Exception:
        logger.exception("Не смог загрузить take-profit state из %s", TAKE_PROFIT_STATE_FILE)
        take_profit_state = {}


def save_take_profit_state() -> None:
    try:
        Path(TAKE_PROFIT_STATE_FILE).write_text(json.dumps(take_profit_state, ensure_ascii=True, indent=2))
    except Exception:
        logger.exception("Не смог сохранить take-profit state в %s", TAKE_PROFIT_STATE_FILE)


def fetch_my_positions(*, force: bool = False) -> list[dict]:
    """Fetch funder positions (for take-profit decisions), with a short TTL cache."""
    global _positions_cache, _positions_cache_at
    if not POLY_FUNDER:
        return []
    now = time.time()
    if not force and _positions_cache_at and now - _positions_cache_at < POSITIONS_CACHE_TTL_SEC:
        return _positions_cache
    results: list[dict] = []
    offset = 0
    limit = 200
    while True:
        url = f"https://data-api.polymarket.com/positions?user={POLY_FUNDER}&limit={limit}&offset={offset}"
        attempts = [(True, "verify")]
        last_error = None
        data = None
        for verify_ssl, _label in attempts:
            try:
                resp = http_session.get(url, timeout=FETCH_TIMEOUT_SEC, verify=verify_ssl)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                last_error = e
        if data is None:
            logger.debug("fetch_my_positions failed: %s", last_error)
            break
        positions = data.get("positions", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        positions = [p for p in positions if isinstance(p, dict)]
        results.extend(positions)
        if len(positions) < limit:
            break
        offset += limit
    _positions_cache = results
    _positions_cache_at = time.time()
    return results


def find_my_position(token_id: str) -> dict | None:
    token_id = str(token_id)
    for position in fetch_my_positions():
        asset = str(
            position.get("asset")
            or position.get("asset_id")
            or position.get("token_id")
            or ""
        )
        if asset == token_id:
            return position
    return None


def sell_allowed_by_profit_rule(token_id: str, sell_price: float, *, market_label: str, reason: str) -> bool:
    if not SELL_ONLY_IN_PROFIT:
        return True

    position = find_my_position(token_id)
    if not position:
        logger.info(
            "⏭ SELL пропущен: не смог подтвердить позицию для проверки профита | %s | %s",
            reason,
            market_label,
        )
        return False

    avg_price = safe_float(position.get("avgPrice"), 0.0)
    size = safe_float(position.get("size"), 0.0)
    if avg_price <= 0 or size <= 0:
        logger.info(
            "⏭ SELL пропущен: нет валидной avgPrice/size для проверки профита | avg=%.4f size=%.2f | %s | %s",
            avg_price,
            size,
            reason,
            market_label,
        )
        return False

    if sell_price <= avg_price:
        logger.info(
            "⏭ SELL пропущен: только в плюс | sell=%.4f avg=%.4f | %s | %s",
            sell_price,
            avg_price,
            reason,
            market_label,
        )
        return False

    return True


def remember_trade_id(trade_id: str) -> None:
    global seen_trade_order
    if not trade_id or trade_id in seen_trade_ids:
        return
    seen_trade_ids.add(trade_id)
    seen_trade_order.append(trade_id)
    if len(seen_trade_order) > SEEN_TRADES_MAX:
        dropped = seen_trade_order[:-SEEN_TRADES_MAX]
        seen_trade_order = seen_trade_order[-SEEN_TRADES_MAX:]
        for old_id in dropped:
            seen_trade_ids.discard(old_id)
    save_seen_trade_ids()


def forget_trade_id(trade_id: str) -> None:
    global seen_trade_order
    if not trade_id or trade_id not in seen_trade_ids:
        return
    seen_trade_ids.discard(trade_id)
    seen_trade_order = [x for x in seen_trade_order if x != trade_id]
    save_seen_trade_ids()


def load_wallets() -> list[WalletRule]:
    path = os.path.join(os.path.dirname(__file__), "wallets.txt")
    wallets: list[WalletRule] = []
    with open(path) as f:
        for line in f:
            line = line.split("#")[0].strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split("|") if part.strip()]
            address = parts[0].lower()
            if not address.startswith("0x"):
                continue
            rule = WalletRule(
                address=address,
                copy_mode=COPY_MODE,
                balance_risk_pct=BALANCE_RISK_PCT,
                fixed_order_size=FIXED_ORDER_SIZE,
                source_size_min=SOURCE_SIZE_MIN,
                source_size_max=SOURCE_SIZE_MAX,
            )
            for part in parts[1:]:
                if "=" not in part:
                    continue
                key, value = [x.strip().lower() for x in part.split("=", 1)]
                try:
                    if key == "mode":
                        rule.copy_mode = value
                    elif key == "balance_risk_pct":
                        rule.balance_risk_pct = float(value)
                    elif key == "fixed_size":
                        rule.fixed_order_size = float(value)
                    elif key == "source_min":
                        rule.source_size_min = float(value)
                    elif key == "source_max":
                        rule.source_size_max = float(value)
                except ValueError:
                    logger.warning("Некорректное значение правила кошелька: %s", part)
            wallets.append(rule)
    if WATCH_ONLY_WALLET:
        wallets = [w for w in wallets if w.address == WATCH_ONLY_WALLET]
        if not wallets:
            wallets = [
                WalletRule(
                    address=WATCH_ONLY_WALLET,
                    copy_mode=COPY_MODE,
                    balance_risk_pct=BALANCE_RISK_PCT,
                    fixed_order_size=FIXED_ORDER_SIZE,
                    source_size_min=SOURCE_SIZE_MIN,
                    source_size_max=SOURCE_SIZE_MAX,
                )
            ]
    logger.info("Отслеживаю %d кошельков", len(wallets))
    return wallets


def fetch_recent_trades(wallet: str) -> list[dict]:
    """Получить последние трейды кошелька через Polymarket API."""
    url = f"https://data-api.polymarket.com/activity?user={wallet}&limit={RECENT_TRADES_LIMIT}"
    attempts = [(True, "verify")]
    last_error = None
    retries = max(1, FETCH_RETRIES + 1)
    for verify_ssl, label in attempts:
        for attempt in range(1, retries + 1):
            try:
                response = http_session.get(url, timeout=FETCH_TIMEOUT_SEC, verify=verify_ssl)
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, list) else []
            except Exception as e:
                last_error = e
                if attempt < retries:
                    time.sleep(HTTP_BACKOFF_SEC * attempt)
                else:
                    logger.debug(
                        "fetch_recent_trades failed for %s via %s attempt %d/%d: %s",
                        wallet[:10],
                        label,
                        attempt,
                        retries,
                        e,
                    )
    logger.warning("Ошибка при получении трейдов %s: %s", wallet[:10], last_error)
    return []


def write_health(status: str, **extra: object) -> None:
    payload = {
        "status": status,
        "pid": os.getpid(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    try:
        Path(HEALTH_FILE).write_text(json.dumps(payload, ensure_ascii=True, indent=2))
    except Exception:
        logger.exception("Не смог обновить health file %s", HEALTH_FILE)


def build_clob_client() -> ClobClient:
    creds = ApiCreds(
        api_key=POLY_API_KEY,
        api_secret=POLY_SECRET,
        api_passphrase=POLY_PASS,
    )
    return ClobClient(
        host="https://clob.polymarket.com",
        key=POLY_KEY,
        chain_id=POLYGON,
        creds=creds,
        signature_type=SIGNATURE_TYPE,
        funder=POLY_FUNDER or None,
    )


def get_clob_balance_usdc(clob: ClobClient) -> float:
    params = BalanceAllowanceParams(
        asset_type=AssetType.COLLATERAL,
        signature_type=SIGNATURE_TYPE,
    )
    last_error = None
    for attempt in range(1, 3):
        try:
            balance = clob.get_balance_allowance(params).get("balance", "0")
            return int(balance) / 1_000_000
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(0.75 * attempt)
    raise last_error


def ensure_collateral_allowance(clob: ClobClient, *, force: bool = False) -> bool:
    """Make sure Polymarket trading contracts can spend collateral."""
    params = BalanceAllowanceParams(
        asset_type=AssetType.COLLATERAL,
        signature_type=SIGNATURE_TYPE,
    )
    try:
        state = clob.get_balance_allowance(params)
        allowances = state.get("allowances") or {}
        missing = force or any(int(v or "0") == 0 for v in allowances.values())
        if not missing:
            return False
        logger.warning("🔐 Обновляю collateral allowance для CLOB...")
        result = clob.update_balance_allowance(params)
        logger.info("✅ Collateral allowance обновлён: %s", result)
        return True
    except Exception as e:
        logger.warning("Не смог обновить collateral allowance: %s", e)
        return False


def try_get_clob_balance_usdc(clob: ClobClient) -> float | None:
    try:
        return get_clob_balance_usdc(clob)
    except Exception as e:
        logger.warning("Не смог получить баланс CLOB: %s", e)
        return None


def get_position_size(clob: ClobClient, token_id: str) -> float:
    params = BalanceAllowanceParams(
        asset_type=AssetType.CONDITIONAL,
        token_id=token_id,
        signature_type=SIGNATURE_TYPE,
    )
    balance = clob.get_balance_allowance(params).get("balance", "0")
    return int(balance) / 1_000_000


def calc_order_size(
    price: float,
    source_size: float,
    source_notional: float,
    copy_mode: str,
    balance_risk_pct: float,
    fixed_order_size: float,
    available_balance: float | None = None,
) -> tuple[float, float]:
    """Return (shares, estimated USDC notional) using fixed, balance-percent, or source-fraction sizing."""
    if copy_mode == "fixed" and fixed_order_size > 0:
        shares = max(EXCHANGE_MIN_ORDER_SIZE, MIN_ORDER_SIZE, fixed_order_size)
        shares = round(shares, 2)
        return shares, round(shares * price, 4)

    if copy_mode == "balance_percent" and balance_risk_pct > 0 and available_balance is not None:
        target_notional = max(0.0, available_balance * balance_risk_pct)
        min_notional_shares = MIN_ORDER_NOTIONAL / price if price > 0 else 0.0
        shares = max(
            EXCHANGE_MIN_ORDER_SIZE,
            MIN_ORDER_SIZE,
            target_notional / price if price > 0 else 0.0,
            min_notional_shares,
        )
        shares = round(shares, 2)
        return shares, round(shares * price, 4)

    proportional_shares = max(0.0, source_size * COPY_TRADE_FRACTION)
    proportional_notional = max(0.0, source_notional * COPY_TRADE_FRACTION)
    min_notional_shares = MIN_ORDER_NOTIONAL / price
    shares = max(
        EXCHANGE_MIN_ORDER_SIZE,
        MIN_ORDER_SIZE,
        proportional_shares,
        proportional_notional / price if price > 0 else 0.0,
        min_notional_shares,
    )
    shares = round(shares, 2)
    return shares, round(shares * price, 4)


def floor_shares(value: float) -> float:
    return math.floor(value * 100) / 100


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def round_down_to_tick(price: float, tick: float) -> float:
    if tick and tick > 0:
        steps = math.floor(price / tick)
        return round(steps * tick, 4)
    return round(price, 4)


def parse_not_enough_balance(err_text: str) -> tuple[float | None, float | None, float | None]:
    """
    Parse Polymarket error like:
      "not enough balance / allowance: ... balance: 1843051, sum of matched orders: 2480000, order amount: 1760000"
    Returns (balance_usd, reserved_usd, order_usd) as floats or None.
    Values are in micro-USDC in the message.
    """
    try:
        m = re.search(r"balance:\s*(\d+),\s*sum of matched orders:\s*(\d+),\s*order amount:\s*(\d+)", err_text)
        if not m:
            return None, None, None
        bal = int(m.group(1)) / 1_000_000
        reserved = int(m.group(2)) / 1_000_000
        order_amt = int(m.group(3)) / 1_000_000
        return bal, reserved, order_amt
    except Exception:
        return None, None, None


def has_live_order(clob: ClobClient, token_id: str, side: str) -> bool:
    """Не ставить дубль, если такой ордер уже висит в стакане."""
    orders = None
    for attempt in range(1, 3):
        try:
            orders = clob.get_open_orders(OpenOrderParams())
            break
        except Exception as e:
            logger.warning("Не смог проверить открытые ордера (попытка %d/2): %s", attempt, e)
            if attempt < 2:
                time.sleep(0.5)
    if orders is None:
        # После ретраев так и не смогли проверить — считаем, что ордер может висеть:
        # лучше пропустить тик, чем поставить дубль и удвоить экспозицию.
        return True

    for order in orders:
        if str(order.get("asset_id")) != str(token_id):
            continue
        if str(order.get("side", "")).upper() != side:
            continue
        if str(order.get("status", "")).upper() != "LIVE":
            continue
        original = float(order.get("original_size") or 0)
        matched = float(order.get("size_matched") or 0)
        if original - matched > 0:
            return True
    return False


def get_tick_size(clob: ClobClient, token_id: str) -> float:
    try:
        book = clob.get_order_book(token_id)
        tick = getattr(book, "tick_size", None)
        if tick is None and isinstance(book, dict):
            tick = book.get("tick_size")
        return safe_float(tick, 0.0)
    except Exception:
        return 0.0


def get_best_bid(clob: ClobClient, token_id: str) -> tuple[float | None, float]:
    """Return (best_bid_price, best_bid_size)."""
    try:
        book = clob.get_order_book(token_id)
        raw_bids = getattr(book, "bids", None)
        if raw_bids is None and isinstance(book, dict):
            raw_bids = book.get("bids")

        bids: list[tuple[float, float]] = []
        if raw_bids:
            for b in raw_bids:
                if isinstance(b, dict):
                    price = b.get("price")
                    size = b.get("size")
                else:
                    price = getattr(b, "price", None)
                    size = getattr(b, "size", None)
                if price is None:
                    continue
                bids.append((float(price), float(size or 0)))

        if not bids:
            return None, 0.0
        return max(bids, key=lambda x: x[0])
    except Exception:
        return None, 0.0


def close_position_immediately(
    clob: ClobClient,
    token_id: str,
    size: float,
    *,
    market_label: str,
    reason: str,
) -> bool:
    """
    Try to close by selling into best bid using FAK (fill-and-kill) so remainder doesn't hang.
    """
    best_bid, bid_size = get_best_bid(clob, token_id)
    if best_bid is None or best_bid <= 0:
        logger.info("⏭ Не вижу best bid, не могу закрыть сразу | %s", market_label)
        return False

    tick = get_tick_size(clob, token_id)
    price = round_down_to_tick(float(best_bid), tick)
    if not sell_allowed_by_profit_rule(token_id, price, market_label=market_label, reason=reason):
        return False
    sell_size = floor_shares(size)
    if sell_size < MIN_SELL_ORDER_SIZE or sell_size * price < MIN_ORDER_NOTIONAL:
        logger.info("⏭ Слишком маленькая позиция для закрытия: %.4f shares | %s", sell_size, market_label)
        return False

    logger.warning(
        "🏁 CLOSE NOW: SELL @ %.4f | %.2f shares | best_bid_size=%.2f | %s | %s",
        price,
        sell_size,
        bid_size,
        market_label,
        reason,
    )
    if PROFIT_TAKE_DRY_RUN:
        logger.info("🧪 PROFIT_TAKE_DRY_RUN=1 — close ордер не отправляю.")
        return True

    try:
        # Remove any hanging SELL limits for this market first.
        try:
            clob.cancel_market_orders(token_id)
        except Exception:
            pass

        order_args = OrderArgs(token_id=token_id, price=round(price, 4), size=sell_size, side="SELL")
        signed = clob.create_order(order_args)
        result = clob.post_order(signed, OrderType.FAK)
        logger.warning("🏁 Close result: %s", result)
        return True
    except Exception as e:
        logger.exception("❌ Close NOW failed: %s | %s", e, market_label)
        return False


def place_limit_order(
    clob: ClobClient,
    token_id: str,
    side: str,
    price: float,
    size: float,
    *,
    reason: str,
    market_label: str,
    cancel_remainder: bool = True,
) -> str | None:
    try:
        side = side.upper()
        order_key = (token_id, side)
        last_order_at = recent_order_times.get(order_key)
        if last_order_at and time.time() - last_order_at < ORDER_COOLDOWN_SEC:
            logger.info("⏭ Cooldown: %s | %s", reason, market_label)
            return None

        if has_live_order(clob, token_id, side):
            logger.info("⏭ Уже есть LIVE %s ордер (%s), пропускаю | %s", side, reason, market_label)
            return None

        if not is_market_active(clob, token_id):
            logger.info("⏭ Маркет закрыт (%s), пропускаю | %s", reason, market_label)
            return None
        if side == "SELL" and not sell_allowed_by_profit_rule(token_id, price, market_label=market_label, reason=reason):
            return None

        notional = round(size * price, 4)
        logger.info(
            "🧾 Ордер: %s @ %.4f | %.2f shares (~$%.2f) | %s | %s",
            side,
            price,
            size,
            notional,
            market_label,
            reason,
        )
        if TAKE_PROFIT_DRY_RUN:
            logger.info("🧪 TAKE_PROFIT_DRY_RUN=1 — ордер не отправляю.")
            return "DRY_RUN"

        order_args = OrderArgs(token_id=token_id, price=round(price, 4), size=size, side=side)
        signed = clob.create_order(order_args)
        order_type = getattr(OrderType, COPY_ORDER_TYPE, OrderType.GTC)
        result = clob.post_order(signed, order_type)
        recent_order_times[order_key] = time.time()
        logger.info("✅ Ордер размещён (%s): %s", reason, result)
        order_id = result.get("orderID") or result.get("orderId") or result.get("id")
        if order_id and cancel_remainder:
            track_background_task(asyncio.create_task(cancel_live_remainder(clob, order_id, token_id)))
        return str(order_id) if order_id else None
    except Exception as e:
        logger.exception("❌ Ошибка ордера (%s): %s", reason, e)
        return None


async def cancel_live_remainder(clob: ClobClient, order_id: str, token_id: str) -> None:
    """After trying to copy an executed trader order, wait a bit, then remove rest."""
    if ORDER_FILL_WAIT_SEC > 0:
        logger.info(
            "⏳ Жду %ds перед проверкой остатка ордера: %s",
            ORDER_FILL_WAIT_SEC,
            order_id,
        )
        await asyncio.sleep(ORDER_FILL_WAIT_SEC)

    try:
        order = clob.get_order(order_id)
    except Exception as e:
        logger.warning("Не смог проверить ордер %s после размещения: %s", order_id, e)
        return

    status = str(order.get("status", "")).upper()
    if status != "LIVE":
        return

    original = float(order.get("original_size") or 0)
    matched = float(order.get("size_matched") or 0)
    remaining = original - matched
    if remaining <= 0:
        return

    logger.info("🧹 Остаток ордера %.4f shares остался LIVE, отменяю: %s", remaining, order_id)
    logger.info("🧹 Cancel result: %s", clob.cancel_order(order_id))


async def place_auto_sell_after_buy(
    clob: ClobClient,
    token_id: str,
    bought_size: float,
    market_label: str,
) -> None:
    if not AUTO_SELL_AFTER_BUY_ENABLE or AUTO_SELL_AFTER_BUY_PRICE <= 0:
        return
    logger.info(
        "🎯 AUTO SELL: жду %ds после BUY, затем попробую выставить SELL @ %.2f | %s",
        AUTO_SELL_AFTER_BUY_DELAY_SEC,
        AUTO_SELL_AFTER_BUY_PRICE,
        market_label,
    )
    if AUTO_SELL_AFTER_BUY_DELAY_SEC > 0:
        await asyncio.sleep(AUTO_SELL_AFTER_BUY_DELAY_SEC)

    try:
        position = floor_shares(min(get_position_size(clob, token_id), bought_size))
    except Exception as e:
        logger.warning("Не смог проверить позицию перед авто-продажей @ %.2f | %s | %s",
                       AUTO_SELL_AFTER_BUY_PRICE, market_label, e)
        return

    if position < MIN_SELL_ORDER_SIZE or position * AUTO_SELL_AFTER_BUY_PRICE < MIN_ORDER_NOTIONAL:
        logger.info(
            "⏭ Авто-продажа @ %.2f пропущена: позиция %.2f shares слишком мала | %s",
            AUTO_SELL_AFTER_BUY_PRICE,
            position,
            market_label,
        )
        return

    tick = get_tick_size(clob, token_id)
    price = round_down_to_tick(AUTO_SELL_AFTER_BUY_PRICE, tick)
    reason = f"auto_sell_after_buy @ {price:.2f}"
    order_id = place_limit_order(
        clob,
        token_id=token_id,
        side="SELL",
        price=price,
        size=position,
        reason=reason,
        market_label=market_label,
        cancel_remainder=False,
    )
    if order_id:
        logger.info(
            "🎯 AUTO SELL POSTED: SELL @ %.2f | %.2f shares | order_id=%s | %s",
            price,
            position,
            order_id,
            market_label,
        )
    else:
        logger.info(
            "⏭ AUTO SELL SKIPPED: не удалось выставить SELL @ %.2f | %.2f shares | %s",
            price,
            position,
            market_label,
        )


def track_background_task(task: asyncio.Task) -> None:
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


def resolve_token_id(clob: ClobClient, condition_id: str, outcome: str) -> str | None:
    """Найти token_id в CLOB по conditionId и outcome (Yes/No/Up/Down)."""
    try:
        market = clob.get_market(condition_id)
        tokens = market.get("tokens", [])
        outcome_lower = outcome.lower()
        for t in tokens:
            if t.get("outcome", "").lower() == outcome_lower:
                return t["token_id"]
        # outcome не совпал ни с одним токеном — НЕ угадываем (иначе можно
        # купить противоположный исход). Пропускаем сделку.
        logger.warning(
            "❌ Не удалось сопоставить outcome '%s' с токенами рынка %s (%s)",
            outcome,
            condition_id[:16],
            [t.get("outcome") for t in tokens],
        )
        return None
    except Exception as e:
        logger.debug("resolve_token_id error: %s", e)
    return None


def is_market_active(clob: ClobClient, token_id: str) -> bool:
    """Проверить что маркет открыт и торгуется."""
    try:
        clob.get_order_book(token_id)
        return True
    except Exception:
        return False


def is_short_term_market(market: str) -> bool:
    market_lower = market.lower()
    return any(
        x in market_lower
        for x in ["5pm", "12:0", "pm-", "am-", ":05", ":10", ":15", ":30", ":45", "up or down"]
    )


def market_matches_keywords(market: str) -> bool:
    if not ALLOWED_MARKET_KEYWORDS:
        return True
    market_lower = market.lower()
    return any(keyword in market_lower for keyword in ALLOWED_MARKET_KEYWORDS)


async def execute_copy_trade(clob: ClobClient, trade: dict, wallet_rule: WalletRule) -> bool:
    """Скопировать сделку через CLOB API."""
    try:
        wallet = wallet_rule.address
        outcome    = trade.get("outcome", "")
        side       = trade.get("side", "BUY").upper()
        price      = float(trade.get("price") or 0)
        source_size = safe_float(trade.get("size"), 0.0)
        source_notional = safe_float(trade.get("usdcSize"), source_size * price)
        market     = trade.get("market") or trade.get("title", "")[:50]

        # conditionId из поля asset_id или напрямую
        condition_id = (trade.get("conditionId")
                        or trade.get("asset_id")
                        or trade.get("market_id") or "")

        if not condition_id or price <= 0:
            logger.warning("Нет conditionId или цены: %s", trade)
            return False

        # Найти правильный token_id
        token_id = resolve_token_id(clob, condition_id, outcome)
        if not token_id:
            logger.warning("❌ Не найден token_id для %s / %s", condition_id[:16], outcome)
            return False

        order_key = (token_id, side)
        last_order_at = recent_order_times.get(order_key)
        if last_order_at and time.time() - last_order_at < ORDER_COOLDOWN_SEC:
            logger.info(
                "⏭ Cooldown %.0fs: уже недавно ставил %s по этому рынку | %s",
                ORDER_COOLDOWN_SEC - (time.time() - last_order_at), side, market,
            )
            return False

        if has_live_order(clob, token_id, side):
            logger.info("⏭ Уже есть LIVE %s ордер по этому рынку, дубль не ставлю | %s", side, market)
            return False

        # Проверить что маркет активен
        if not is_market_active(clob, token_id):
            logger.warning("⏭ Маркет закрыт, пропускаю: %s", market)
            return False

        if side == "BUY":
            balance = get_clob_balance_usdc(clob)
            effective_available = max(0.0, balance - max(0.0, RESERVE_CASH_USD))
            size, notional = calc_order_size(
                price,
                source_size,
                source_notional,
                wallet_rule.copy_mode,
                wallet_rule.balance_risk_pct,
                wallet_rule.fixed_order_size,
                effective_available,
            )
            need = notional + max(0.0, BALANCE_BUFFER_USD)
            if need > effective_available:
                logger.info(
                    "⏭ Недостаточно USDC.e: нужно ~$%.2f (вкл. buffer $%.2f), доступно $%.2f (reserve $%.2f) | %s",
                    need,
                    BALANCE_BUFFER_USD,
                    effective_available,
                    RESERVE_CASH_USD,
                    market,
                )
                return False
        else:
            if not COPY_SELLS:
                logger.info("⏭ SELL пропущен: COPY_SELLS=0")
                return False

            position = get_position_size(clob, token_id)
            balance = try_get_clob_balance_usdc(clob)
            effective_available = max(0.0, (balance or 0.0) - max(0.0, RESERVE_CASH_USD))
            target_size, _ = calc_order_size(
                price,
                source_size,
                source_notional,
                wallet_rule.copy_mode,
                wallet_rule.balance_risk_pct,
                wallet_rule.fixed_order_size,
                effective_available,
            )
            size = floor_shares(min(position, target_size))
            notional = round(size * price, 4)
            min_sell_size = max(MIN_SELL_ORDER_SIZE, EXCHANGE_MIN_ORDER_SIZE)
            if size < min_sell_size or notional < MIN_ORDER_NOTIONAL:
                logger.info(
                    "⏭ SELL пропущен: позиция %.2f shares, можно %.2f (~$%.2f), минимум %.2f shares и $%.2f | %s",
                    position, size, notional, min_sell_size, MIN_ORDER_NOTIONAL, market,
                )
                return False
            if not sell_allowed_by_profit_rule(token_id, price, market_label=market, reason="copy trader SELL"):
                return False

        logger.info(
            "🔄 Копирую: %s %s @ %.4f | %.2f shares (~$%.2f) | %s",
            side, outcome, price, size, notional, market
        )

        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 4),
            size=size,
            side=side,
        )
        signed = clob.create_order(order_args)
        order_type = getattr(OrderType, COPY_ORDER_TYPE, OrderType.GTC)
        try:
            result = clob.post_order(signed, order_type)
        except Exception as e:
            msg = str(e)
            if "not enough balance / allowance" in msg:
                ensure_collateral_allowance(clob, force=True)
                bal, reserved, order_amt = parse_not_enough_balance(msg)
                if bal is not None:
                    logger.warning(
                        "⏭ Недостаточно свободного USDC.e (часть занята): balance=%.2f reserved=%.2f order=%.2f | %s",
                        bal,
                        (reserved or 0.0),
                        (order_amt or 0.0),
                        market,
                    )
                else:
                    logger.warning("⏭ Недостаточно свободного USDC.e (часть занята) | %s", market)
                # Avoid spamming the same market every tick.
                recent_order_times[order_key] = time.time()
                return False
            raise
        recent_order_times[order_key] = time.time()
        logger.info("✅ Ордер размещён: %s", result)
        order_id = result.get("orderID") or result.get("orderId") or result.get("id")
        if order_id:
            track_background_task(asyncio.create_task(cancel_live_remainder(clob, order_id, token_id)))
        if side == "BUY":
            track_background_task(asyncio.create_task(
                place_auto_sell_after_buy(clob, token_id, size, market)
            ))
        return True

    except Exception as e:
        logger.exception("❌ Ошибка ордера: %s", e)
        return False


async def notify(text: str):
    """Уведомить в Telegram если ADMIN_CHAT_ID задан."""
    if not config.ADMIN_CHAT_ID:
        return
    try:
        from telegram import Bot
        bot = Bot(token=config.BOT_TOKEN)
        await bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=text,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning("Telegram уведомление не отправлено: %s", e)


async def monitor_wallet(clob: ClobClient, wallet_rule: WalletRule):
    """Проверить новые трейды кошелька и скопировать."""
    wallet = wallet_rule.address
    trades = await asyncio.to_thread(fetch_recent_trades, wallet)
    for trade in trades:
        trade_id = str(trade.get("id") or trade.get("transactionHash") or "")
        if not trade_id or trade_id in seen_trade_ids:
            continue

        remember_trade_id(trade_id)

        side   = trade.get("side", "").upper()
        amount = float(trade.get("usdcSize") or trade.get("size") or 0)
        market = trade.get("market") or trade.get("title", "")[:50]
        price  = float(trade.get("price") or 0)

        if side not in ("BUY", "SELL"):
            continue

        source_size = safe_float(trade.get("size"), 0.0)
        if wallet_rule.source_size_min > 0 and source_size < wallet_rule.source_size_min:
            logger.info("⏭ Сделка меньше SOURCE_SIZE_MIN %.2f shares: %.2f | %s", wallet_rule.source_size_min, source_size, market)
            continue
        if wallet_rule.source_size_max > 0 and source_size > wallet_rule.source_size_max:
            logger.info("⏭ Сделка больше SOURCE_SIZE_MAX %.2f shares: %.2f | %s", wallet_rule.source_size_max, source_size, market)
            continue

        if not market_matches_keywords(market):
            logger.info("⏭ Сигнал вне списка рынков (%s), пропускаю: %s", ",".join(ALLOWED_MARKET_KEYWORDS), market)
            continue

        # Пропускаем краткосрочные маркеты (5-минутки, hourly)
        short_term = is_short_term_market(market)
        if short_term and wallet.lower() != SHORT_TERM_WALLET:
            logger.info("⏭ Краткосрочный маркет, пропускаю: %s", market)
            continue
        if (
            short_term
            and not COPY_ALL_SHORT_TERM_FOR_WALLET
            and not (SHORT_TERM_MIN_PRICE <= price <= SHORT_TERM_MAX_PRICE)
        ):
            logger.info(
                "⏭ Краткосрочный сигнал вне диапазона %.1f–%.1f¢: %.1f¢ | %s",
                SHORT_TERM_MIN_PRICE * 100,
                SHORT_TERM_MAX_PRICE * 100,
                price * 100,
                market,
            )
            continue
        if short_term:
            logger.info("⚡ Краткосрочный маркет разрешён для %s...%s: %s", wallet[:6], wallet[-4:], market)

        emoji = "🟢" if side == "BUY" else "🔴"
        logger.info(
            "%s Сигнал от %s...%s: %s @ %.2f¢ $%.0f | %s",
            emoji, wallet[:6], wallet[-4:], side, price * 100, amount, market
        )

        await notify(
            f"🎯 *Сигнал от топ-кошелька*\n"
            f"{emoji} {side} @ {price*100:.1f}¢ — инсайдер: ${amount:,.0f}\n"
            f"👤 `{wallet}`\n"
            f"📊 {market}\n"
            f"⏳ Копирую {wallet_rule.balance_risk_pct*100:.2f}% баланса..." if wallet_rule.copy_mode == "balance_percent"
            else (
                f"⏳ Копирую fixed {wallet_rule.fixed_order_size:.2f} shares..." if wallet_rule.copy_mode == "fixed"
                else f"⏳ Копирую {COPY_TRADE_FRACTION*100:.1f}% сделки..."
            )
        )

        success = await execute_copy_trade(clob, trade, wallet_rule)

        if success:
            await notify(f"✅ Скопировано: {side} {market[:40]}")
        else:
            await notify(f"❌ Не удалось скопировать: {side} {market[:40]}")


def maybe_place_take_profit_orders(clob: ClobClient) -> None:
    """
    Rule requested by user:
    Once a position is up >= TAKE_PROFIT_TRIGGER_PCT (e.g. +20%) vs avg entry,
    place a limit SELL at avg*(1+TAKE_PROFIT_LOCK_PCT) (e.g. +10%) to lock profit.
    """
    if not TAKE_PROFIT_ENABLE:
        return
    positions = fetch_my_positions(force=True)
    if not positions:
        return

    changed = False
    placed = 0
    for p in positions:
        token_id = str(p.get("asset") or "")
        if not token_id or not token_id.isdigit():
            continue

        avg = safe_float(p.get("avgPrice"), 0.0)
        cur = safe_float(
            p.get("curPrice")
            or p.get("currentPrice")
            or p.get("cur_price")
            or p.get("price"),
            0.0,
        )
        size = safe_float(p.get("size"), 0.0)
        title = str(p.get("title") or p.get("market") or "")[:80]

        if avg <= 0 or cur <= 0 or size <= 0:
            continue

        profit_pct = (cur / avg) - 1.0
        st = take_profit_state.setdefault(token_id, {})
        if st.get("tp_disabled"):
            continue
        prev_max = safe_float(st.get("max_profit_pct"), 0.0)
        if profit_pct > prev_max:
            st["max_profit_pct"] = profit_pct
            changed = True

        if st.get("tp_placed"):
            continue

        if profit_pct < TAKE_PROFIT_TRIGGER_PCT:
            continue

        # Place once (full close)
        target_price = avg * (1.0 + TAKE_PROFIT_LOCK_PCT)
        tick = get_tick_size(clob, token_id)
        price = round_down_to_tick(target_price, tick)
        sell_size = floor_shares(size)
        notional = sell_size * price

        if sell_size < MIN_ORDER_SIZE or notional < MIN_ORDER_NOTIONAL:
            continue

        reason = f"take-profit: +{TAKE_PROFIT_LOCK_PCT*100:.0f}% after +{TAKE_PROFIT_TRIGGER_PCT*100:.0f}%"
        market_label = title or f"token_id={token_id}"
        order_id = place_limit_order(
            clob,
            token_id=token_id,
            side="SELL",
            price=price,
            size=sell_size,
            reason=reason,
            market_label=market_label,
            cancel_remainder=False,
        )
        if order_id:
            st["tp_placed"] = True
            st["tp_price"] = price
            st["entry_avg"] = avg
            st["placed_at"] = datetime.now(timezone.utc).isoformat()
            st["tp_order_id"] = order_id
            changed = True
            placed += 1
            if placed >= TAKE_PROFIT_MAX_PER_CHECK:
                break

    # Прунинг: убираем состояние по позициям, которых больше нет (закрыты/проданы),
    # чтобы take_profit_state и его JSON-файл не росли без ограничения.
    live_tokens = {
        str(p.get("asset"))
        for p in positions
        if str(p.get("asset") or "").isdigit() and safe_float(p.get("size"), 0.0) > 0
    }
    for stale in [t for t in take_profit_state if t not in live_tokens]:
        del take_profit_state[stale]
        changed = True

    if changed:
        save_take_profit_state()


def maybe_close_big_profit_positions(clob: ClobClient) -> None:
    """
    User rule:
    If profit on a position is >= PROFIT_TAKE_USD, close it immediately.
    """
    if (
        (not PROFIT_TAKE_ENABLE or PROFIT_TAKE_USD <= 0)
        and (not LOW_ENTRY_TAKE_PROFIT_ENABLE or LOW_ENTRY_TAKE_PROFIT_USD <= 0)
    ):
        return

    positions = fetch_my_positions(force=True)
    if not positions:
        return

    closed = 0
    for p in positions:
        token_id = str(p.get("asset") or "")
        if not token_id or not token_id.isdigit():
            continue

        size = safe_float(p.get("size"), 0.0)
        if size <= 0:
            continue

        avg = safe_float(p.get("avgPrice"), 0.0)
        # Prefer data-api cashPnl if present.
        profit_usd = safe_float(p.get("cashPnl"), 0.0)
        if profit_usd == 0.0:
            cur = safe_float(
                p.get("curPrice")
                or p.get("currentPrice")
                or p.get("cur_price")
                or p.get("price"),
                0.0,
            )
            if avg > 0 and cur > 0:
                profit_usd = (cur - avg) * size

        low_entry_hit = (
            LOW_ENTRY_TAKE_PROFIT_ENABLE
            and LOW_ENTRY_TAKE_PROFIT_USD > 0
            and avg >= LOW_ENTRY_MIN_AVG
            and avg <= LOW_ENTRY_MAX_AVG
            and profit_usd >= LOW_ENTRY_TAKE_PROFIT_USD
        )
        normal_hit = PROFIT_TAKE_ENABLE and PROFIT_TAKE_USD > 0 and profit_usd >= PROFIT_TAKE_USD

        if not (low_entry_hit or normal_hit):
            continue

        title = str(p.get("title") or p.get("market") or "")[:80]
        market_label = title or f"token_id={token_id}"
        if not is_market_active(clob, token_id):
            continue

        if low_entry_hit:
            reason = (
                f"low_entry_take_profit: avg={avg:.4f} in [{LOW_ENTRY_MIN_AVG:.2f},{LOW_ENTRY_MAX_AVG:.2f}] "
                f"profit>=${LOW_ENTRY_TAKE_PROFIT_USD:.0f} (profit=${profit_usd:.2f})"
            )
        else:
            reason = f"profit_take_usd>=${PROFIT_TAKE_USD:.0f} (profit=${profit_usd:.2f})"

        ok = close_position_immediately(
            clob,
            token_id=token_id,
            size=size,
            market_label=market_label,
            reason=reason,
        )
        if ok:
            closed += 1
            if closed >= PROFIT_TAKE_MAX_PER_CHECK:
                break


async def main():
    print("BOOT: entering main", flush=True)
    acquire_single_instance_lock()
    print("BOOT: lock acquired", flush=True)
    atexit.register(release_single_instance_lock)
    database.init_db()
    print("BOOT: db initialized", flush=True)
    load_seen_trade_ids()
    load_take_profit_state()
    wallets = load_wallets()
    logger.info("Стартую copy_trader: wallets=%d, seen=%d", len(wallets), len(seen_trade_ids))
    write_health("starting", wallets=len(wallets), seen=len(seen_trade_ids))
    logger.info("Создаю CLOB client...")
    clob = build_clob_client()
    logger.info("CLOB client создан. Проверяю баланс...")
    balance = try_get_clob_balance_usdc(clob)
    if balance is None:
        logger.warning("Не удалось получить баланс на старте. Продолжаю и попробую позже.")
        balance = 0.0
    if balance <= 0:
        logger.error(
            "CLOB collateral balance is 0. Пополни USDC (Polygon) (%s), затем запусти снова.",
            clob.get_collateral_address(),
        )
        logger.info("Продолжаю мониторинг несмотря на нулевой/недоступный баланс.")
    short_term_mode = (
        f"all for {SHORT_TERM_WALLET}"
        if SHORT_TERM_WALLET and COPY_ALL_SHORT_TERM_FOR_WALLET
        else (
            f"{SHORT_TERM_MIN_PRICE * 100:.1f}–{SHORT_TERM_MAX_PRICE * 100:.1f}¢ for {SHORT_TERM_WALLET}"
            if SHORT_TERM_WALLET
            else "disabled"
        )
    )
    logger.info(
        "Баланс CLOB: $%.2f | copy mode: %s | balance risk: %.2f%% | fixed size: %.2f | copy fraction: %.2f%% | source size filter: %.2f-%.2f | min size: %.2f shares | min notional: $%.2f | order_type: %s | cooldown: %ds | short-term: %s | COPY_SELLS=%s",
        balance,
        COPY_MODE,
        BALANCE_RISK_PCT * 100,
        FIXED_ORDER_SIZE,
        COPY_TRADE_FRACTION * 100,
        SOURCE_SIZE_MIN,
        SOURCE_SIZE_MAX,
        MIN_ORDER_SIZE,
        MIN_ORDER_NOTIONAL,
        COPY_ORDER_TYPE,
        ORDER_COOLDOWN_SEC,
        short_term_mode,
        int(COPY_SELLS),
    )
    logger.info("Ожидание перед отменой хвоста ордера: %ds", ORDER_FILL_WAIT_SEC)
    logger.info("CLOB подключён. Начинаю мониторинг %d кошельков каждые %ds",
                len(wallets), POLL_INTERVAL)
    write_health(
        "warming_up",
        wallets=len(wallets),
        seen=len(seen_trade_ids),
        cash_est=balance,
        poll_interval=POLL_INTERVAL,
    )

    # Прогреть seen_ids текущими трейдами, но при желании оставить хвост
    # последних трейдов для догонки после рестарта.
    logger.info("Загружаю историю трейдов (чтобы не копировать старые)...")
    for idx, wallet_rule in enumerate(wallets, start=1):
        w = wallet_rule.address
        logger.info("Warmup %d/%d: %s...%s", idx, len(wallets), w[:6], w[-4:])
        try:
            trades = fetch_recent_trades(w)
            backlog_keep = max(0, min(BACKFILL_RECENT_TRADES, len(trades)))
            if backlog_keep > 0:
                logger.info(
                    "Warmup: оставляю последние %d трейдов для догонки после рестарта | %s...%s",
                    backlog_keep,
                    w[:6],
                    w[-4:],
                )
            cutoff = len(trades) - backlog_keep
            for t in trades[:cutoff]:
                tid = str(t.get("id") or t.get("transactionHash") or "")
                if tid:
                    remember_trade_id(tid)
            if backlog_keep > 0:
                for t in trades[cutoff:]:
                    tid = str(t.get("id") or t.get("transactionHash") or "")
                    if tid:
                        forget_trade_id(tid)
        except Exception:
            logger.exception("Warmup failed for %s", w)
    logger.info("Загружено %d известных трейдов. Жду новых...", len(seen_trade_ids))
    write_health(
        "running",
        wallets=len(wallets),
        seen=len(seen_trade_ids),
        cash_est=balance,
        poll_interval=POLL_INTERVAL,
    )

    last_heartbeat = 0.0
    last_known_balance = balance
    last_tp_check = 0.0
    last_profit_take_check = 0.0
    while True:
        try:
            now = time.time()
            if HEARTBEAT_SEC > 0 and now - last_heartbeat >= HEARTBEAT_SEC:
                fresh_balance = try_get_clob_balance_usdc(clob)
                if fresh_balance is not None:
                    last_known_balance = fresh_balance
                logger.info(
                    "💓 Heartbeat | wallets=%d | seen=%d | cooldown=%ds | cash_est=%.2f",
                    len(wallets),
                    len(seen_trade_ids),
                    ORDER_COOLDOWN_SEC,
                    last_known_balance,
                )
                write_health(
                    "running",
                    wallets=len(wallets),
                    seen=len(seen_trade_ids),
                    cash_est=last_known_balance,
                    cooldown=ORDER_COOLDOWN_SEC,
                )
                last_heartbeat = now

            profit_take_loop_enabled = (
                (PROFIT_TAKE_ENABLE and PROFIT_TAKE_USD > 0)
                or (LOW_ENTRY_TAKE_PROFIT_ENABLE and LOW_ENTRY_TAKE_PROFIT_USD > 0)
            )
            if profit_take_loop_enabled and PROFIT_TAKE_CHECK_SEC > 0 and now - last_profit_take_check >= PROFIT_TAKE_CHECK_SEC:
                try:
                    await asyncio.to_thread(maybe_close_big_profit_positions, clob)
                except Exception:
                    logger.exception("Profit-take loop error")
                last_profit_take_check = now

            if TAKE_PROFIT_ENABLE and TAKE_PROFIT_CHECK_SEC > 0 and now - last_tp_check >= TAKE_PROFIT_CHECK_SEC:
                try:
                    await asyncio.to_thread(maybe_place_take_profit_orders, clob)
                except Exception:
                    logger.exception("Take-profit loop error")
                last_tp_check = now

            for wallet_rule in wallets:
                wallet = wallet_rule.address
                try:
                    await monitor_wallet(clob, wallet_rule)
                except Exception:
                    logger.exception("Ошибка мониторинга кошелька %s", wallet)
                    write_health(
                        "wallet_error",
                        wallet=wallet,
                        wallets=len(wallets),
                        seen=len(seen_trade_ids),
                        cash_est=last_known_balance,
                    )
                await asyncio.sleep(0.5)   # пауза между кошельками
            await asyncio.sleep(POLL_INTERVAL)
        except Exception:
            logger.exception("Главный цикл упал, пробую продолжить")
            write_health(
                "loop_error",
                wallets=len(wallets),
                seen=len(seen_trade_ids),
                cash_est=last_known_balance,
            )
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
