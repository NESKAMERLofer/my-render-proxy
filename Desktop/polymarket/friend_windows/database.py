import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Any

import config


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            wallet      TEXT NOT NULL,
            action      TEXT NOT NULL,   -- BUY | SELL
            asset       TEXT NOT NULL,
            price_cents REAL NOT NULL,   -- 0-100
            amount_usd  REAL NOT NULL,
            market      TEXT,
            insider_type TEXT,
            note        TEXT,
            raw         TEXT
        );

        CREATE TABLE IF NOT EXISTS wallet_stats (
            wallet          TEXT PRIMARY KEY,
            first_seen      TEXT,
            last_seen       TEXT,
            total_trades    INTEGER DEFAULT 0,
            buy_count       INTEGER DEFAULT 0,
            sell_count      INTEGER DEFAULT 0,
            buy_volume      REAL DEFAULT 0,
            sell_volume     REAL DEFAULT 0,
            realized_pnl    REAL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(wallet);
        CREATE INDEX IF NOT EXISTS idx_trades_ts     ON trades(ts);
        """)


def insert_trade(signal) -> int:
    ts = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO trades
               (ts, wallet, action, asset, price_cents, amount_usd, market,
                insider_type, note, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ts, signal.wallet, signal.action, signal.asset,
             signal.price_cents, signal.amount_usd, signal.market,
             signal.insider_type, signal.note, signal.raw),
        )
        trade_id = cur.lastrowid
        _update_wallet_stats(conn, signal, ts)
        return trade_id


def _update_wallet_stats(conn, signal, ts: str):
    existing = conn.execute(
        "SELECT * FROM wallet_stats WHERE wallet=?", (signal.wallet,)
    ).fetchone()

    if existing is None:
        conn.execute(
            """INSERT INTO wallet_stats
               (wallet, first_seen, last_seen, total_trades,
                buy_count, sell_count, buy_volume, sell_volume, realized_pnl)
               VALUES (?,?,?,1,?,?,?,?,0)""",
            (
                signal.wallet, ts, ts,
                1 if signal.action == 'BUY' else 0,
                1 if signal.action == 'SELL' else 0,
                signal.amount_usd if signal.action == 'BUY' else 0,
                signal.amount_usd if signal.action == 'SELL' else 0,
            ),
        )
    else:
        buy_count  = existing['buy_count']  + (1 if signal.action == 'BUY'  else 0)
        sell_count = existing['sell_count'] + (1 if signal.action == 'SELL' else 0)
        buy_vol    = existing['buy_volume'] + (signal.amount_usd if signal.action == 'BUY'  else 0)
        sell_vol   = existing['sell_volume']+ (signal.amount_usd if signal.action == 'SELL' else 0)
        # Simple realized PnL: sell_volume - buy_volume (approximate)
        realized_pnl = sell_vol - buy_vol
        conn.execute(
            """UPDATE wallet_stats SET
               last_seen=?, total_trades=total_trades+1,
               buy_count=?, sell_count=?, buy_volume=?, sell_volume=?,
               realized_pnl=?
               WHERE wallet=?""",
            (ts, buy_count, sell_count, buy_vol, sell_vol, realized_pnl, signal.wallet),
        )


def get_wallet_trades(wallet: str) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE wallet=? ORDER BY ts DESC", (wallet,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_wallet_stats(wallet: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM wallet_stats WHERE wallet=?", (wallet,)
        ).fetchone()
        return dict(row) if row else None


def get_all_wallets() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wallet_stats ORDER BY total_trades DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_top_wallets(by: str = "realized_pnl", limit: int = 10) -> List[Dict]:
    allowed = {"realized_pnl", "total_trades", "buy_volume"}
    col = by if by in allowed else "realized_pnl"
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM wallet_stats ORDER BY {col} DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_trades(limit: int = 20) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_global_stats() -> Dict[str, Any]:
    with get_conn() as conn:
        total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        total_wallets = conn.execute("SELECT COUNT(*) FROM wallet_stats").fetchone()[0]
        total_volume = conn.execute(
            "SELECT COALESCE(SUM(amount_usd),0) FROM trades"
        ).fetchone()[0]
        buy_vol = conn.execute(
            "SELECT COALESCE(SUM(amount_usd),0) FROM trades WHERE action='BUY'"
        ).fetchone()[0]
        sell_vol = conn.execute(
            "SELECT COALESCE(SUM(amount_usd),0) FROM trades WHERE action='SELL'"
        ).fetchone()[0]
        top_market = conn.execute(
            """SELECT market, COUNT(*) as cnt FROM trades
               WHERE market != '' GROUP BY market ORDER BY cnt DESC LIMIT 1"""
        ).fetchone()
    return {
        "total_trades": total_trades,
        "total_wallets": total_wallets,
        "total_volume": total_volume,
        "buy_volume": buy_vol,
        "sell_volume": sell_vol,
        "top_market": dict(top_market) if top_market else None,
    }
