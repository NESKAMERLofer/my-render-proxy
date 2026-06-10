import os
import re
import sys
import time
from dataclasses import dataclass

import requests
from dotenv import load_dotenv
from web3 import Web3

from py_clob_client.config import get_contract_config
from py_clob_client.constants import POLYGON


load_dotenv()


POLY_KEY = (os.getenv("POLY_PRIVATE_KEY") or "").strip()
FUNDER = (os.getenv("POLY_FUNDER_ADDRESS") or "").strip()
RPC = (os.getenv("POLYGON_RPC_URL") or "").strip()


RPC_CANDIDATES = [
    RPC,
    "https://polygon-bor-rpc.publicnode.com",
    # "https://polygon.llamarpc.com",  # can fail to resolve in some networks
]


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def build_web3() -> Web3:
    last_err = None
    for rpc in [r for r in RPC_CANDIDATES if r]:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
        try:
            if w3.eth.chain_id == POLYGON:
                return w3
            last_err = RuntimeError(f"{rpc} returned chain_id={w3.eth.chain_id}")
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Не удалось подключиться к Polygon RPC: {last_err}")


def fetch_redeemable_positions(address: str, limit: int = 500) -> list[dict]:
    url = "https://data-api.polymarket.com/positions"
    sess = requests.Session()
    sess.headers.update({"User-Agent": "polymarket-redeem-all/1.0"})
    out: list[dict] = []
    offset = 0
    while True:
        params = {"user": address, "redeemable": "true", "limit": limit, "offset": offset}
        rows = None
        last_err = None
        for attempt in range(1, 6):
            for verify in (True,):
                try:
                    r = sess.get(url, params=params, timeout=20, verify=verify)
                    r.raise_for_status()
                    rows = r.json()
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
            if rows is not None:
                break
            time.sleep(min(2.0, 0.35 * attempt))
        if rows is None:
            die(f"data-api fetch failed: {last_err}")

        positions = (
            rows.get("positions", [])
            if isinstance(rows, dict)
            else (rows if isinstance(rows, list) else [])
        )
        out.extend([p for p in positions if isinstance(p, dict)])
        if len(positions) < limit:
            break
        offset += limit
    return out


@dataclass
class Pos:
    condition_id: str
    token_id: int
    outcome_index: int
    title: str


def norm_condition_id(cond: str) -> str:
    cond = (cond or "").strip()
    if not cond:
        return ""
    if cond.startswith("0x") and len(cond) == 66:
        return cond.lower()
    if re.fullmatch(r"[0-9a-fA-F]{64}", cond):
        return ("0x" + cond).lower()
    return cond.lower()


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


def to_b32(cond: str) -> bytes:
    cond = norm_condition_id(cond)
    if not (cond.startswith("0x") and len(cond) == 66):
        die(f"conditionId не bytes32: {cond}")
    return bytes.fromhex(cond[2:])


def match_index_set(ct, parent: bytes, cond_b32: bytes, collateral: str, token_id: int, outcome_index: int) -> int | None:
    # Primary guess (binary & multi-outcome: indexSet is 1<<outcomeIndex)
    guess = 1 << int(outcome_index)
    try:
        col = ct.functions.getCollectionId(parent, cond_b32, guess).call()
        pos = ct.functions.getPositionId(collateral, col).call()
        if int(pos) == int(token_id):
            return guess
    except Exception:
        pass

    # Fallback: brute small range (covers typical markets)
    for oi in range(0, 16):
        g = 1 << oi
        try:
            col = ct.functions.getCollectionId(parent, cond_b32, g).call()
            pos = ct.functions.getPositionId(collateral, col).call()
            if int(pos) == int(token_id):
                return g
        except Exception:
            continue
    return None


def fee_params(w3: Web3) -> tuple[int, int]:
    gas_price = int(w3.eth.gas_price)
    min_tip = int(w3.to_wei(25, "gwei"))
    max_priority = max(min_tip, int(gas_price * 0.25))
    max_fee = max(gas_price, int(max_priority * 2))
    return max_fee, max_priority


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    if not POLY_KEY:
        die("Нет POLY_PRIVATE_KEY в .env")
    if not FUNDER:
        die("Нет POLY_FUNDER_ADDRESS в .env")

    w3 = build_web3()
    acct = w3.eth.account.from_key(POLY_KEY)
    if acct.address.lower() != FUNDER.lower():
        die(f"Приватный ключ ведёт на {acct.address}, а FUNDER={FUNDER} (не совпадают)")

    cfg = get_contract_config(POLYGON, neg_risk=False)
    ct_addr = Web3.to_checksum_address(cfg.conditional_tokens)
    collateral = Web3.to_checksum_address(cfg.collateral)
    ct = w3.eth.contract(address=ct_addr, abi=CT_ABI)

    positions_raw = fetch_redeemable_positions(FUNDER)
    positions: list[Pos] = []
    for p in positions_raw:
        cond = norm_condition_id(p.get("conditionId") or p.get("condition_id") or "")
        asset = p.get("asset") or p.get("tokenId") or p.get("positionId")
        oi = p.get("outcomeIndex")
        if not cond or asset is None or oi is None:
            continue
        try:
            token_id = int(asset)
            outcome_index = int(oi)
        except Exception:
            continue
        title = (p.get("title") or p.get("market") or "").strip()
        positions.append(Pos(condition_id=cond, token_id=token_id, outcome_index=outcome_index, title=title))

    if not positions:
        print("Нет redeemable позиций (data-api).")
        return 0

    # group by condition
    by_cond: dict[str, list[Pos]] = {}
    for pos in positions:
        by_cond.setdefault(pos.condition_id, []).append(pos)

    funder = Web3.to_checksum_address(FUNDER)
    parent = b"\x00" * 32

    claim_plan: list[tuple[str, bytes, list[int], str]] = []
    for cond, items in by_cond.items():
        try:
            cond_b32 = to_b32(cond)
            den = ct.functions.payoutDenominator(cond_b32).call()
            if int(den) == 0:
                continue  # not resolved onchain
            idxs: list[int] = []
            title = items[0].title
            for it in items:
                # only bother if balance > 0
                bal = ct.functions.balanceOf(funder, int(it.token_id)).call()
                if int(bal) <= 0:
                    continue
                idx = match_index_set(ct, parent, cond_b32, collateral, it.token_id, it.outcome_index)
                if idx is not None:
                    idxs.append(int(idx))
            idxs = sorted(set(idxs))
            if idxs:
                claim_plan.append((cond, cond_b32, idxs, title))
        except Exception:
            continue

    print(f"Redeemable (data-api): {len(positions_raw)} positions")
    print(f"Resolved+balance>0: {len(claim_plan)} conditions to redeem")
    for cond, _, idxs, title in claim_plan[:25]:
        print(f"- {cond[:10]} indexSets={idxs} {title[:80]}")
    if len(claim_plan) > 25:
        print(f"... +{len(claim_plan) - 25} more")

    if not claim_plan:
        return 0

    if dry_run:
        print("Dry-run: транзакции не отправляю (--dry-run).")
        return 0

    matic = w3.eth.get_balance(funder)
    if int(matic) == 0:
        die("На кошельке нет MATIC на газ.")

    # send one tx per condition (safe + simple)
    sent = 0
    failed = 0
    # Use pending nonce and increment locally to avoid "nonce too low" on fast bursts.
    nonce = w3.eth.get_transaction_count(funder, "pending")
    for cond, cond_b32, idxs, title in claim_plan:
        tx_call = ct.functions.redeemPositions(collateral, parent, cond_b32, idxs)
        gas = tx_call.estimate_gas({"from": funder})
        max_fee, max_priority = fee_params(w3)
        tx = tx_call.build_transaction(
            {
                "from": funder,
                "nonce": nonce,
                "gas": int(gas * 1.25),
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": max_priority,
                "chainId": POLYGON,
            }
        )
        signed = w3.eth.account.sign_transaction(tx, private_key=POLY_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"sent {tx_hash.hex()} indexSets={idxs} {title[:80]}")
        rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=240)
        print(f"receipt {rcpt.status} block {rcpt.blockNumber}")
        nonce += 1
        if rcpt.status == 1:
            sent += 1
        else:
            failed += 1
            print(f"  ⚠ redeem REVERTED on-chain: {tx_hash.hex()} | {title[:80]}")
        time.sleep(0.2)

    print(f"Done. txs_sent={sent} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
