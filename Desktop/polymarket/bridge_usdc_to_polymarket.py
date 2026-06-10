#!/usr/bin/env python3
"""
Bridge native Polygon USDC to a Polymarket account using the official Polymarket Bridge API.

What it does:
1. Requests an official EVM deposit address for POLY_FUNDER_ADDRESS from bridge.polymarket.com
2. Sends Polygon native USDC from the wallet derived from POLY_PRIVATE_KEY to that deposit address

Safe defaults:
- default mode is dry-run
- use --execute to actually send

Examples:
  source venv/bin/activate
  python bridge_usdc_to_polymarket.py --amount 2
  python bridge_usdc_to_polymarket.py --amount 2 --execute
  python bridge_usdc_to_polymarket.py --send-rest --reserve-usdc 0.50 --execute
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
BRIDGE_API_URL = "https://bridge.polymarket.com/deposit"
USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")

RPC_CANDIDATES = [
    os.getenv("POLYGON_RPC_URL", "").strip(),
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
]

ERC20_ABI = [
    {
        "name": "transfer",
        "type": "function",
        "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", type=Decimal, help="USDC amount to send, e.g. 2")
    parser.add_argument("--send-rest", action="store_true", help="Send full available USDC balance minus reserve")
    parser.add_argument("--reserve-usdc", type=Decimal, default=Decimal("0.00"), help="Reserve this much USDC when using --send-rest")
    parser.add_argument("--execute", action="store_true", help="Actually broadcast the transfer transaction")
    parser.add_argument("--show-address-only", action="store_true", help="Only print the official Polymarket deposit address and exit")
    return parser.parse_args()


def get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        die(f"Missing env var {name}")
    return value


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


def to_base_units(amount: Decimal, decimals: int) -> int:
    quantized = amount.quantize(Decimal(10) ** -decimals, rounding=ROUND_DOWN)
    return int(quantized * (10 ** decimals))


def from_base_units(amount: int, decimals: int) -> Decimal:
    return Decimal(amount) / (Decimal(10) ** decimals)


def get_polymarket_deposit_address(target_wallet: str) -> str:
    response = requests.post(BRIDGE_API_URL, json={"address": target_wallet}, timeout=30)
    if response.status_code != 201:
        die(f"Bridge API error {response.status_code}: {response.text[:1000]}")
    data = response.json()
    evm = ((data.get("address") or {}).get("evm") or "").strip()
    if not evm:
        die(f"Bridge API returned no EVM address: {json.dumps(data)}")
    return Web3.to_checksum_address(evm)


def send_tx(w3: Web3, private_key: str, tx: dict) -> str:
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    hex_hash = tx_hash.hex()
    print(f"Transfer sent: {hex_hash}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        die(f"Transfer failed on-chain: {hex_hash}")
    print(f"Transfer confirmed in block {receipt.blockNumber}")
    return hex_hash


def main() -> None:
    args = parse_args()
    if not args.show_address_only and not args.send_rest and args.amount is None:
        die("Specify either --amount 2 or --send-rest")
    if args.send_rest and args.amount is not None:
        die("Use either --amount or --send-rest, not both")

    private_key = get_env("POLY_PRIVATE_KEY")
    target_wallet = get_env("POLY_FUNDER_ADDRESS")

    signer = Account.from_key(private_key).address
    print(f"Source signer wallet: {signer}")
    print(f"Target Polymarket wallet: {target_wallet}")

    deposit_address = get_polymarket_deposit_address(target_wallet)
    print(f"Official Polymarket EVM deposit address: {deposit_address}")

    if args.show_address_only:
        return

    w3 = build_web3()
    token = w3.eth.contract(address=USDC_NATIVE, abi=ERC20_ABI)
    decimals = token.functions.decimals().call()
    raw_balance = token.functions.balanceOf(signer).call()
    wallet_balance = from_base_units(raw_balance, decimals)
    print(f"Source USDC balance: {wallet_balance}")

    if args.send_rest:
        amount = wallet_balance - args.reserve_usdc
    else:
        amount = args.amount

    if amount is None or amount <= 0:
        die(f"Computed transfer amount is not positive: {amount}")
    if amount > wallet_balance:
        die(f"Not enough USDC: have {wallet_balance}, need {amount}")

    raw_amount = to_base_units(amount, decimals)
    print(f"Planned transfer: {amount} USDC")

    pol_balance = w3.eth.get_balance(signer)
    print(f"Source POL balance for gas: {Web3.from_wei(pol_balance, 'ether')}")

    tx = token.functions.transfer(deposit_address, raw_amount).build_transaction({
        "from": signer,
        "chainId": CHAIN_ID,
        "nonce": w3.eth.get_transaction_count(signer, "pending"),
    })

    estimated_gas = w3.eth.estimate_gas(tx)
    fee_history = w3.eth.fee_history(1, "latest", [50])
    base_fee = fee_history["baseFeePerGas"][-1]
    priority_fee = w3.to_wei(40, "gwei")
    max_fee = base_fee + priority_fee * 2

    tx["gas"] = estimated_gas
    tx["maxFeePerGas"] = max_fee
    tx["maxPriorityFeePerGas"] = priority_fee

    print("Prepared transfer tx:")
    print(json.dumps({
        "to": deposit_address,
        "token": USDC_NATIVE,
        "amount_usdc": str(amount),
        "gas": estimated_gas,
        "maxFeePerGas": str(max_fee),
        "maxPriorityFeePerGas": str(priority_fee),
    }, indent=2))

    if not args.execute:
        print("Dry run only. Re-run with --execute to broadcast.")
        return

    send_tx(w3, private_key, tx)


if __name__ == "__main__":
    main()
