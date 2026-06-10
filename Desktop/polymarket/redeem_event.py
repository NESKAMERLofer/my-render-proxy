import json
import os
import re
import sys
from dataclasses import dataclass

import requests
from dotenv import load_dotenv
from web3 import Web3

from py_clob_client.config import get_contract_config
from py_clob_client.constants import POLYGON


load_dotenv()


POLY_KEY = os.getenv("POLY_PRIVATE_KEY", "").strip()
FUNDER = os.getenv("POLY_FUNDER_ADDRESS", "").strip()
RPC = os.getenv("POLYGON_RPC_URL", "").strip()


RPC_CANDIDATES = [
    RPC,
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
]


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def build_web3() -> Web3:
    last_error = None
    for rpc in [r for r in RPC_CANDIDATES if r]:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
        try:
            if w3.eth.chain_id == POLYGON:
                return w3
            last_error = RuntimeError(f"{rpc} returned chain_id={w3.eth.chain_id}")
        except Exception as e:
            last_error = e
    raise RuntimeError(f"Не удалось подключиться к Polygon RPC: {last_error}")


def parse_slug(s: str) -> str:
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        # expected: .../event/<slug>/<slug>
        m = re.search(r"/event/([^/]+)", s)
        if not m:
            die("Не смог извлечь slug из ссылки.")
        return m.group(1)
    return s


@dataclass(frozen=True)
class MarketInfo:
    slug: str
    condition_id: str  # 0x + 64 hex
    outcomes: list[str]
    outcome_prices: list[str]
    clob_token_ids: list[int]


def fetch_market_info(slug: str) -> MarketInfo:
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    sess = requests.Session()
    sess.headers.update({"User-Agent": "polymarket-redeem/1.0"})
    last_err = None
    events = None
    for attempt in range(1, 5):
        for verify in (True,):
            try:
                r = sess.get(url, timeout=15, verify=verify)
                r.raise_for_status()
                events = r.json()
                last_err = None
                break
            except Exception as e:
                last_err = e
        if events is not None:
            break
        # small backoff
        import time

        time.sleep(min(2.5, 0.4 * attempt))
    if events is None:
        die(f"gamma-api fetch failed: {last_err}")
    if not events:
        die(f"gamma-api: событие не найдено по slug={slug}")
    e = events[0]
    markets = e.get("markets") or []
    if not markets:
        die("gamma-api: у события нет markets")
    # For this ticker type it's 1 market.
    m = markets[0]
    condition_id = m.get("conditionId")
    outcomes = m.get("outcomes") or []
    outcome_prices = m.get("outcomePrices") or []
    clob_token_ids_raw = m.get("clobTokenIds") or []

    # gamma-api sometimes returns these as JSON-encoded strings.
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if isinstance(outcome_prices, str):
        outcome_prices = json.loads(outcome_prices)
    if isinstance(clob_token_ids_raw, str):
        clob_token_ids_raw = json.loads(clob_token_ids_raw)
    if not (condition_id and outcomes and outcome_prices and clob_token_ids_raw):
        die("gamma-api: не хватает полей conditionId/outcomes/outcomePrices/clobTokenIds")
    clob_token_ids = [int(x) for x in clob_token_ids_raw]
    return MarketInfo(
        slug=slug,
        condition_id=condition_id,
        outcomes=outcomes,
        outcome_prices=outcome_prices,
        clob_token_ids=clob_token_ids,
    )


CT_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getCollectionId",
        "type": "function",
        "stateMutability": "pure",
        "inputs": [
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSet", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    {
        "name": "getPositionId",
        "type": "function",
        "stateMutability": "pure",
        "inputs": [{"name": "collateralToken", "type": "address"}, {"name": "collectionId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "payoutDenominator",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "conditionId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "payoutNumerators",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "conditionId", "type": "bytes32"}, {"name": "index", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "redeemPositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "outputs": [],
    },
]


def to_b32(hex32: str) -> bytes:
    hex32 = hex32.lower()
    if not hex32.startswith("0x") or len(hex32) != 66:
        die(f"conditionId не bytes32: {hex32}")
    return bytes.fromhex(hex32[2:])


def main() -> int:
    if not POLY_KEY:
        die("Нет POLY_PRIVATE_KEY в .env")
    if not FUNDER:
        die("Нет POLY_FUNDER_ADDRESS в .env")

    if len(sys.argv) < 2:
        die("Usage: python redeem_event.py <event_url_or_slug> [--dry-run]")

    slug = parse_slug(sys.argv[1])
    dry_run = "--dry-run" in sys.argv[2:]

    info = fetch_market_info(slug)

    # Determine winning outcome from gamma outcomePrices (resolved markets are 1/0).
    win_idx = None
    for i, p in enumerate(info.outcome_prices):
        try:
            if float(p) >= 0.999:
                win_idx = i
                break
        except Exception:
            continue
    if win_idx is None:
        die(f"Не вижу победителя в outcomePrices={info.outcome_prices} (рынок, возможно, ещё не resolved)")

    win_outcome = info.outcomes[win_idx]
    win_token_id = info.clob_token_ids[win_idx]

    w3 = build_web3()
    acct = w3.eth.account.from_key(POLY_KEY)
    if acct.address.lower() != FUNDER.lower():
        die(f"Приватный ключ ведёт на {acct.address}, а FUNDER={FUNDER} (не совпадают)")

    cfg = get_contract_config(POLYGON, neg_risk=False)
    ct_addr = Web3.to_checksum_address(cfg.conditional_tokens)
    collateral = Web3.to_checksum_address(cfg.collateral)
    ct = w3.eth.contract(address=ct_addr, abi=CT_ABI)

    funder = Web3.to_checksum_address(FUNDER)
    matic = w3.eth.get_balance(funder)
    print(f"Event: {info.slug}")
    print(f"ConditionId: {info.condition_id}")
    print(f"Winning: {win_outcome}")
    print(f"ConditionalTokens: {ct_addr}")
    print(f"Collateral (USDC.e): {collateral}")
    print(f"Wallet: {acct.address}")
    print(f"MATIC balance: {w3.from_wei(matic, 'ether')}")

    # Check if condition is reported onchain.
    cond_b32 = to_b32(info.condition_id)
    denom = ct.functions.payoutDenominator(cond_b32).call()
    print(f"payoutDenominator: {denom}")
    if denom == 0:
        die("На контракте ConditionalTokens payoutDenominator=0 (ещё не зарезолвлено ончейн). Попробуй позже.")

    # Determine indexSet mapping by recomputing positionIds for binary (1 and 2).
    parent = b"\x00" * 32
    expected = {int(t): None for t in info.clob_token_ids}
    for index_set in (1, 2):
        col = ct.functions.getCollectionId(parent, cond_b32, index_set).call()
        pos = ct.functions.getPositionId(collateral, col).call()
        if int(pos) in expected:
            expected[int(pos)] = index_set

    win_index_set = expected.get(int(win_token_id))
    if not win_index_set:
        die("Не смог сопоставить tokenId -> indexSet (что-то нестандартное в этом рынке).")

    bal = ct.functions.balanceOf(funder, int(win_token_id)).call()
    # amounts in collateral token units (USDC has 6 decimals).
    shares = bal / 1_000_000
    print(f"Winning tokenId balance: {bal} (~{shares:.6f} shares)")
    if bal == 0:
        print("Нечего клеймить: баланс winning-токена = 0")
        return 0

    tx = ct.functions.redeemPositions(
        collateral,
        parent,
        cond_b32,
        [int(win_index_set)],
    )

    try:
        gas = tx.estimate_gas({"from": funder})
    except Exception as e:
        die(f"estimateGas failed (redeem, вероятно, недоступен): {e}")
    print(f"Estimated gas: {gas}")

    if dry_run:
        print("Dry-run: транзакцию не отправляю (--dry-run).")
        return 0

    if matic == 0:
        die("На кошельке нет MATIC на газ. Пополни MATIC и повтори.")

    # Build and send tx.
    nonce = w3.eth.get_transaction_count(funder)
    gas_price = w3.eth.gas_price
    min_tip = w3.to_wei(25, "gwei")
    # Polygon nodes sometimes enforce a minimum priority fee (tip cap).
    max_priority = max(min_tip, int(gas_price * 0.25))
    max_fee = max(gas_price, int(max_priority * 2))
    tx_dict = tx.build_transaction(
        {
            "from": funder,
            "nonce": nonce,
            "gas": int(gas * 1.25),
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": max_priority,
            "chainId": POLYGON,
        }
    )
    signed = w3.eth.account.sign_transaction(tx_dict, private_key=POLY_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Sent redeem tx: {tx_hash.hex()}")
    print("Жду подтверждения...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    print(f"Receipt status: {receipt.status} | block: {receipt.blockNumber}")
    return 0 if receipt.status == 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
