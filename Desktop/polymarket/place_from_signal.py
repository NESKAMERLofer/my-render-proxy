#!/usr/bin/env python3

import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

from py_clob_client_v2 import OrderArgs, OrderType

from copy_trader import (
    build_clob_client,
    floor_shares,
    get_clob_balance_usdc,
    get_tick_size,
    is_market_active,
    round_down_to_tick,
)

MIN_ORDER_NOTIONAL = 1.0
MIN_ORDER_SIZE = 5.0
BALANCE_BUFFER_USD = 0.05

logging.getLogger().setLevel(logging.WARNING)


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def clamp_price(price: float) -> float:
    return min(0.99, max(0.001, round(price, 4)))


def normalize_order(raw_order: dict) -> dict:
    token_id = str(raw_order.get("token_id") or "").strip()
    condition_id = str(raw_order.get("condition_id") or "").strip() or None
    market_slug = str(raw_order.get("market_slug") or "").strip() or None
    market_title = str(raw_order.get("market_title") or "").strip() or None
    label = str(raw_order.get("label") or raw_order.get("key") or token_id).strip()
    price = clamp_price(safe_float(raw_order.get("price"), 0.0))
    stake_usd = round(max(0.0, safe_float(raw_order.get("stake_usd"), 0.0)), 4)
    return {
      "token_id": token_id,
      "condition_id": condition_id,
      "market_slug": market_slug,
      "market_title": market_title,
      "label": label,
      "price": price,
      "stake_usd": stake_usd,
    }


def build_order_result(order: dict, **extra) -> dict:
    payload = {
        "token_id": order["token_id"],
        "condition_id": order["condition_id"],
        "market_slug": order["market_slug"],
        "market_title": order["market_title"],
        "label": order["label"],
        "price": order["price"],
        "stake_usd": order["stake_usd"],
    }
    payload.update(extra)
    return payload


def process_orders(payload: dict) -> dict:
    dry_run = bool(payload.get("dry_run", True))
    requested_orders = payload.get("orders") or []
    orders = [normalize_order(item) for item in requested_orders if isinstance(item, dict)]

    result = {
        "ok": True,
        "dry_run": dry_run,
        "bankroll_usd": safe_float(payload.get("bankroll_usd"), 0.0),
        "orders": [],
        "starting_balance_usd": 0.0,
        "remaining_balance_usd": 0.0,
        "placed_count": 0,
        "skipped_count": 0,
    }

    if not orders:
        result["ok"] = False
        result["error"] = "No orders in payload"
        return result

    clob = build_clob_client()
    available_balance = round(max(0.0, get_clob_balance_usdc(clob)), 4)
    remaining_balance = available_balance
    result["starting_balance_usd"] = available_balance

    for order in orders:
        if not order["token_id"]:
            result["orders"].append(build_order_result(order, status="skipped", reason="Missing token_id"))
            result["skipped_count"] += 1
            continue

        if order["price"] <= 0:
            result["orders"].append(build_order_result(order, status="skipped", reason="Invalid price"))
            result["skipped_count"] += 1
            continue

        if order["stake_usd"] <= 0:
            result["orders"].append(build_order_result(order, status="skipped", reason="Invalid stake_usd"))
            result["skipped_count"] += 1
            continue

        if not is_market_active(clob, order["token_id"]):
            result["orders"].append(build_order_result(order, status="skipped", reason="Market inactive"))
            result["skipped_count"] += 1
            continue

        tick = get_tick_size(clob, order["token_id"]) or 0.001
        price = clamp_price(round_down_to_tick(order["price"], tick))
        shares = floor_shares(order["stake_usd"] / price)
        notional = round(shares * price, 4)

        if shares < MIN_ORDER_SIZE:
            result["orders"].append(
                build_order_result(
                    order,
                    status="skipped",
                    reason="Below Polymarket minimum size",
                    min_required_stake_usd=round(MIN_ORDER_SIZE * price, 4),
                    shares=shares,
                    notional_usd=notional,
                )
            )
            result["skipped_count"] += 1
            continue

        if notional < MIN_ORDER_NOTIONAL:
            result["orders"].append(
                build_order_result(
                    order,
                    status="skipped",
                    reason="Below Polymarket minimum notional",
                    min_required_stake_usd=MIN_ORDER_NOTIONAL,
                    shares=shares,
                    notional_usd=notional,
                )
            )
            result["skipped_count"] += 1
            continue

        if notional + BALANCE_BUFFER_USD > remaining_balance:
            result["orders"].append(
                build_order_result(
                    order,
                    status="skipped",
                    reason="Insufficient balance",
                    shares=shares,
                    notional_usd=notional,
                    available_balance_usd=round(remaining_balance, 4),
                )
            )
            result["skipped_count"] += 1
            continue

        if dry_run:
            remaining_balance = round(max(0.0, remaining_balance - notional), 4)
            result["orders"].append(
                build_order_result(
                    order,
                    status="dry_run",
                    shares=shares,
                    notional_usd=notional,
                    price=price,
                    order_type="FAK",
                )
            )
            result["placed_count"] += 1
            continue

        try:
            signed_order = clob.create_order(
                OrderArgs(
                    token_id=order["token_id"],
                    price=price,
                    size=shares,
                    side="BUY",
                )
            )
            response = clob.post_order(signed_order, OrderType.FAK)
            remaining_balance = round(max(0.0, remaining_balance - notional), 4)
            result["orders"].append(
                build_order_result(
                    order,
                    status="placed",
                    shares=shares,
                    notional_usd=notional,
                    price=price,
                    order_type="FAK",
                    response=response,
                )
            )
            result["placed_count"] += 1
        except Exception as exc:
            result["orders"].append(
                build_order_result(
                    order,
                    status="error",
                    shares=shares,
                    notional_usd=notional,
                    price=price,
                    error=str(exc),
                )
            )
            result["skipped_count"] += 1

    result["remaining_balance_usd"] = remaining_balance
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"Invalid JSON input: {exc}"}))
        return 0

    try:
        result = process_orders(payload)
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "orders": []}

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
