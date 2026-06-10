#!/usr/bin/env python3
"""
Cancel LIVE limit orders on Polymarket CLOB for a given token id (asset_id),
or by matching a position title substring.

Also (by default) disables take-profit auto-placement for the same token(s)
by writing flags into take_profit_state.json.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from py_clob_client.constants import POLYGON


ROOT = Path(__file__).resolve().parent
TAKE_PROFIT_STATE_FILE = Path(os.getenv("TAKE_PROFIT_STATE_FILE", str(ROOT / "take_profit_state.json")))


def build_clob_client() -> ClobClient:
    creds = ApiCreds(
        api_key=os.getenv("POLY_API_KEY", "").strip(),
        api_secret=os.getenv("POLY_SECRET", "").strip(),
        api_passphrase=os.getenv("POLY_PASSPHRASE", "").strip(),
    )
    return ClobClient(
        host="https://clob.polymarket.com",
        key=os.getenv("POLY_PRIVATE_KEY", "").strip(),
        chain_id=POLYGON,
        creds=creds,
        signature_type=int(os.getenv("POLY_SIGNATURE_TYPE", "0")),
        funder=os.getenv("POLY_FUNDER_ADDRESS") or None,
    )


def fetch_positions(funder: str) -> list[dict]:
    sess = requests.Session()
    out: list[dict] = []
    offset = 0
    limit = 200
    while True:
        url = f"https://data-api.polymarket.com/positions?user={funder}&limit={limit}&offset={offset}"
        r = sess.get(url, timeout=15, verify=False)
        r.raise_for_status()
        data = r.json()
        positions = data.get("positions", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        positions = [p for p in positions if isinstance(p, dict)]
        if not positions:
            break
        out.extend(positions)
        if len(positions) < limit:
            break
        offset += limit
    return out


def load_tp_state() -> dict:
    if not TAKE_PROFIT_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(TAKE_PROFIT_STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_tp_state(state: dict) -> None:
    TAKE_PROFIT_STATE_FILE.write_text(json.dumps(state, ensure_ascii=True, indent=2))


def main() -> int:
    load_dotenv()

    p = argparse.ArgumentParser(description="Cancel Polymarket CLOB limit orders by token id or title match.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--token", dest="token_id", help="CLOB token id (asset_id) to cancel orders for")
    g.add_argument("--title", dest="title_substr", help="Substring to match in your position titles")
    p.add_argument("--dry-run", action="store_true", help="Do not cancel, only show what would be done")
    p.add_argument("--no-disable-tp", action="store_true", help="Do not disable take-profit for matched tokens")
    args = p.parse_args()

    funder = os.getenv("POLY_FUNDER_ADDRESS", "").strip()
    if not funder:
        raise SystemExit("Missing POLY_FUNDER_ADDRESS in .env")

    token_ids: list[str] = []
    if args.token_id:
        token_ids = [str(args.token_id).strip()]
    else:
        needle = str(args.title_substr).strip().lower()
        positions = fetch_positions(funder)
        for pos in positions:
            title = str(pos.get("title") or pos.get("market") or "")
            if needle in title.lower():
                tok = str(pos.get("asset") or "")
                if tok and tok.isdigit():
                    token_ids.append(tok)

        token_ids = sorted(set(token_ids))
        if not token_ids:
            raise SystemExit(f"No positions matched title substring: {args.title_substr!r}")

    now = datetime.now(timezone.utc).isoformat()
    tp_state = load_tp_state()

    if args.dry_run:
        print("DRY RUN")
        print("Tokens:", token_ids)
        return 0

    clob = build_clob_client()

    for tok in token_ids:
        print(f"Canceling market orders for token={tok} ...")
        res = clob.cancel_market_orders(tok)
        print(res)

        st = tp_state.get(tok)
        if isinstance(st, dict):
            order_id = st.get("tp_order_id")
            if order_id:
                try:
                    print(f"Canceling tp_order_id={order_id} ...")
                    print(clob.cancel(str(order_id)))
                except Exception as e:
                    print(f"Warning: cancel(tp_order_id) failed: {e}")

        if not args.no_disable_tp:
            st = tp_state.setdefault(tok, {})
            if isinstance(st, dict):
                st["tp_disabled"] = True
                st["tp_disabled_reason"] = "manual_cancel_limit"
                st["tp_disabled_at"] = now

    if not args.no_disable_tp:
        save_tp_state(tp_state)
        print(f"Updated take-profit state: {TAKE_PROFIT_STATE_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

