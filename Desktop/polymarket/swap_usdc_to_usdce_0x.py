#!/usr/bin/env python3
"""
Swap Polygon native USDC -> USDC.e using the official 0x Swap API.

Important:
- This script only works when the wallet holding the tokens is the same wallet
  as the provided private key.
- If your funds are on a Polymarket proxy/funder wallet and your private key
  corresponds to a different signer address, the script exits with a clear error.

Usage:
  source venv/bin/activate
  export ZEROX_API_KEY=...
  python swap_usdc_to_usdce_0x.py --amount 99
  python swap_usdc_to_usdce_0x.py --amount 99 --execute
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, ROUND_DOWN

import requests
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

load_dotenv()

CHAIN_ID = 137
ZEROX_BASE_URL = "https://api.0x.org"
USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")

RPC_CANDIDATES = [
    os.getenv("POLYGON_RPC_URL", "").strip(),
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
]

ERC20_ABI = [
    {
        "name": "approve",
        "type": "function",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "allowance",
        "type": "function",
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [{"name": "owner", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "name": "decimals",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
    },
]


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def build_web3() -> Web3:
    last_error: Exception | None = None
    for rpc in [r for r in RPC_CANDIDATES if r]:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
            if w3.eth.chain_id == CHAIN_ID:
                print(f"RPC: {rpc}")
                return w3
        except Exception as exc:
            last_error = exc
    die(f"Could not connect to Polygon RPC: {last_error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", required=True, type=Decimal, help="Amount of native Polygon USDC to sell")
    parser.add_argument("--slippage-bps", type=int, default=100, help="Max slippage in basis points, default 100 = 1%%")
    parser.add_argument("--execute", action="store_true", help="Actually approve and execute the swap")
    parser.add_argument("--sell-token", default=USDC_NATIVE)
    parser.add_argument("--buy-token", default=USDC_E)
    return parser.parse_args()


def get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        die(f"Missing env var {name}")
    return value


def to_base_units(amount: Decimal, decimals: int) -> int:
    quantized = amount.quantize(Decimal(10) ** -decimals, rounding=ROUND_DOWN)
    return int(quantized * (10 ** decimals))


def send_tx(w3: Web3, private_key: str, tx: dict, *, label: str) -> str:
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    hex_hash = tx_hash.hex()
    print(f"{label} sent: {hex_hash}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        die(f"{label} failed on-chain: {hex_hash}")
    print(f"{label} confirmed in block {receipt.blockNumber}")
    return hex_hash


def main() -> None:
    args = parse_args()
    api_key = get_env("ZEROX_API_KEY")
    private_key = get_env("POLY_PRIVATE_KEY")
    funder = get_env("POLY_FUNDER_ADDRESS")

    signer = Account.from_key(private_key).address
    if signer.lower() != funder.lower():
        die(
            "This script cannot swap from your current setup because the private key "
            f"controls {signer}, while the tokens are expected on proxy/funder {funder}. "
            "0x Swap API returns a normal transaction that must be sent by the wallet holding the tokens."
        )

    w3 = build_web3()
    token = w3.eth.contract(address=Web3.to_checksum_address(args.sell_token), abi=ERC20_ABI)
    sell_decimals = token.functions.decimals().call()
    sell_amount = to_base_units(args.amount, sell_decimals)
    wallet_balance = token.functions.balanceOf(signer).call()
    if wallet_balance < sell_amount:
        die(f"Wallet balance too low: have {wallet_balance / 10**sell_decimals:.6f}, need {args.amount}")

    headers = {
        "0x-api-key": api_key,
        "0x-version": "v2",
    }
    params = {
        "chainId": CHAIN_ID,
        "sellToken": Web3.to_checksum_address(args.sell_token),
        "buyToken": Web3.to_checksum_address(args.buy_token),
        "sellAmount": str(sell_amount),
        "taker": signer,
        "slippageBps": str(args.slippage_bps),
    }
    quote_resp = requests.get(
        f"{ZEROX_BASE_URL}/swap/allowance-holder/quote",
        headers=headers,
        params=params,
        timeout=30,
    )
    if quote_resp.status_code != 200:
        die(f"0x quote failed: {quote_resp.status_code} {quote_resp.text[:1000]}")
    quote = quote_resp.json()

    print("Quote:")
    print(json.dumps({
        "sellAmount": quote.get("sellAmount"),
        "buyAmount": quote.get("buyAmount"),
        "minBuyAmount": quote.get("minBuyAmount"),
        "issues": quote.get("issues"),
        "allowanceTarget": quote.get("allowanceTarget"),
        "route": quote.get("route"),
    }, indent=2))

    spender = (
        ((quote.get("issues") or {}).get("allowance") or {}).get("spender")
        or quote.get("allowanceTarget")
    )
    if not spender:
        die("0x quote did not include an allowance spender")

    allowance = token.functions.allowance(signer, Web3.to_checksum_address(spender)).call()
    print(f"Current allowance for {spender}: {allowance / 10**sell_decimals:.6f}")

    if not args.execute:
        print("Dry run only. Re-run with --execute to approve and swap.")
        return

    nonce = w3.eth.get_transaction_count(signer, "pending")
    fee_history = w3.eth.fee_history(1, "latest", [50])
    base_fee = fee_history["baseFeePerGas"][-1]
    priority_fee = w3.to_wei(40, "gwei")
    max_fee = base_fee + priority_fee * 2

    if allowance < sell_amount:
        approve_tx = token.functions.approve(
            Web3.to_checksum_address(spender),
            sell_amount,
        ).build_transaction({
            "from": signer,
            "chainId": CHAIN_ID,
            "nonce": nonce,
            "gas": 120000,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
        })
        send_tx(w3, private_key, approve_tx, label="Approval")
        nonce += 1

    tx = quote.get("transaction")
    if not tx:
        die("0x quote did not include a transaction payload")

    swap_tx = {
        "from": signer,
        "to": Web3.to_checksum_address(tx["to"]),
        "data": tx["data"],
        "value": int(tx.get("value", "0")),
        "chainId": CHAIN_ID,
        "nonce": nonce,
        "gas": int(tx.get("gas") or 600000),
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority_fee,
    }
    send_tx(w3, private_key, swap_tx, label="Swap")


if __name__ == "__main__":
    main()
