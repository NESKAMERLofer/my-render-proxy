#!/usr/bin/env python3
"""Local stop-loss watcher for the Max Houkes position."""

import asyncio
import logging
import math
import os

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, AssetType, BalanceAllowanceParams, OrderArgs, OrderType
from py_clob_client.constants import POLYGON

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

TOKEN_ID = os.getenv(
    "STOP_LOSS_TOKEN_ID",
    "106687789138651486806803771720629299367411811892935619090783696973974722397190",
)
LABEL = os.getenv("STOP_LOSS_LABEL", "Max Houkes")
STOP_PRICE = float(os.getenv("STOP_LOSS_PRICE", "0.50"))
POLL_INTERVAL = int(os.getenv("STOP_LOSS_POLL_SEC", "5"))
SIGNATURE_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "0"))


def build_client() -> ClobClient:
    creds = ApiCreds(
        api_key=os.getenv("POLY_API_KEY", ""),
        api_secret=os.getenv("POLY_SECRET", ""),
        api_passphrase=os.getenv("POLY_PASSPHRASE", ""),
    )
    return ClobClient(
        host="https://clob.polymarket.com",
        key=os.getenv("POLY_PRIVATE_KEY", ""),
        chain_id=POLYGON,
        creds=creds,
        signature_type=SIGNATURE_TYPE,
        funder=os.getenv("POLY_FUNDER_ADDRESS") or None,
    )


def get_position_size(clob: ClobClient) -> float:
    params = BalanceAllowanceParams(
        asset_type=AssetType.CONDITIONAL,
        token_id=TOKEN_ID,
        signature_type=SIGNATURE_TYPE,
    )
    raw = clob.get_balance_allowance(params).get("balance", "0")
    return int(raw) / 1_000_000


def get_best_bid(clob: ClobClient) -> tuple[float | None, float]:
    book = clob.get_order_book(TOKEN_ID)
    bids = [(float(b.price), float(b.size)) for b in (book.bids or []) if b.price is not None]
    if not bids:
        return None, 0.0
    return max(bids, key=lambda item: item[0])


async def main():
    clob = build_client()
    logger.info("Stop-loss armed: %s, trigger best_bid <= %.4f", LABEL, STOP_PRICE)

    while True:
        try:
            shares = get_position_size(clob)
            bid, bid_size = get_best_bid(clob)
            if shares <= 0:
                logger.info("No %s position left. Stop-loss watcher exits.", LABEL)
                return

            logger.info("%s position %.4f shares | best_bid=%s size=%.2f", LABEL, shares, bid, bid_size)

            if bid is not None and bid <= STOP_PRICE:
                sell_size = math.floor(shares * 100) / 100
                if sell_size <= 0:
                    logger.info("Position %.6f is too small to sell safely. Exiting.", shares)
                    return
                logger.warning(
                    "STOP TRIGGERED: best_bid %.4f <= %.4f. Selling %.2f shares.",
                    bid,
                    STOP_PRICE,
                    sell_size,
                )
                order = OrderArgs(
                    token_id=TOKEN_ID,
                    price=round(bid, 4),
                    size=sell_size,
                    side="SELL",
                )
                signed = clob.create_order(order)
                result = clob.post_order(signed, OrderType.GTC)
                logger.warning("Stop-loss SELL result: %s", result)
                return

        except Exception as e:
            logger.error("Stop-loss watcher error: %s", e)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
