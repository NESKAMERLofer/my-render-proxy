"""
Диагностика: почему CLOB видит balance=0.
Проверяет адрес кошелька, балансы обоих USDC-контрактов, аллоуансы, CLOB-состояние.
"""
import os, json, subprocess
from dotenv import load_dotenv
load_dotenv()

from web3 import Web3
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
from py_clob_client.constants import POLYGON

POLY_KEY  = os.getenv("POLY_PRIVATE_KEY", "")
API_KEY   = os.getenv("POLY_API_KEY", "")
SECRET    = os.getenv("POLY_SECRET", "")
PASSPHRASE= os.getenv("POLY_PASSPHRASE", "")
FUNDER    = os.getenv("POLY_FUNDER_ADDRESS", "")
SIGNATURE_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "0"))

# ── Polygon RPC ──────────────────────────────────────────────────────────────
RPC_CANDIDATES = [
    os.getenv("POLYGON_RPC_URL", "").strip(),
    "https://polygon-rpc.com",
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
]

def build_web3():
    last_error = None
    for rpc in [r for r in RPC_CANDIDATES if r]:
        candidate = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
        try:
            chain_id = candidate.eth.chain_id
            if chain_id == POLYGON:
                print(f"Polygon RPC: {rpc}")
                return candidate
            last_error = RuntimeError(f"{rpc} returned chain_id={chain_id}")
        except Exception as e:
            last_error = e
            print(f"RPC недоступен: {rpc} ({e})")
    raise RuntimeError(f"Не удалось подключиться к Polygon RPC: {last_error}")

w3 = build_web3()

ERC20_ABI = [
    {"name":"balanceOf","type":"function","inputs":[{"name":"a","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},
    {"name":"allowance","type":"function","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},
    {"name":"decimals","type":"function","inputs":[],"outputs":[{"name":"","type":"uint8"}],"stateMutability":"view"},
]

# Оба USDC на Polygon
USDC_NATIVE   = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"   # нативный USDC (Circle)
USDC_BRIDGED  = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"   # USDC.e (bridged)

# Контракты Polymarket
CTF_EXCHANGE   = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEGRISK_ADAPTER= "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
NEGRISK_EXCHANGE="0xC5d563A36AE78145C45a50134d48A1215220f80a"

spenders = {
    "CTF Exchange":    CTF_EXCHANGE,
    "NegRisk Adapter": NEGRISK_ADAPTER,
    "NegRisk Exchange":NEGRISK_EXCHANGE,
}

def check_token(label, addr, owner):
    c = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
    dec = c.functions.decimals().call()
    bal = c.functions.balanceOf(Web3.to_checksum_address(owner)).call()
    print(f"\n── {label} ({addr[:8]}...) ──")
    print(f"   Баланс: {bal / 10**dec:.6f} ({bal} raw)")
    for name, sp in spenders.items():
        al = c.functions.allowance(
            Web3.to_checksum_address(owner),
            Web3.to_checksum_address(sp)
        ).call()
        max_val = 2**256 - 1
        tag = "MAX ✅" if al == max_val else (f"{al/10**dec:.2f}" if al else "0 ❌")
        print(f"   Allowance → {name}: {tag}")

def main():
    # 1. Адрес из приватного ключа
    from eth_account import Account
    account = Account.from_key(POLY_KEY)
    derived_address = account.address.lower()
    print("=" * 60)
    print(f"Приватный ключ → адрес: {account.address}")
    print(f"POLY_FUNDER_ADDRESS:    {FUNDER}")
    if derived_address == FUNDER.lower():
        print("✅ Адреса совпадают")
    else:
        print("❌ АДРЕСА НЕ СОВПАДАЮТ — вот в чём проблема!")
    print("=" * 60)

    # 2. Балансы on-chain
    check_token("Нативный USDC (Circle)",  USDC_NATIVE,  FUNDER or account.address)
    check_token("USDC.e (bridged)",        USDC_BRIDGED, FUNDER or account.address)

    # 3. CLOB баланс
    print("\n── CLOB get_balance_allowance ──")
    creds = ApiCreds(api_key=API_KEY, api_secret=SECRET, api_passphrase=PASSPHRASE)
    clob = ClobClient(
        host="https://clob.polymarket.com",
        key=POLY_KEY,
        chain_id=POLYGON,
        creds=creds,
        signature_type=SIGNATURE_TYPE,
        funder=FUNDER or None,
    )
    print(f"CLOB signer address: {clob.get_address()}")
    print(f"CLOB collateral:     {clob.get_collateral_address()}")

    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=SIGNATURE_TYPE)
    result = clob.get_balance_allowance(params)
    print(f"get_balance_allowance: {json.dumps(result, indent=2)}")

    print("\n── CLOB update_balance_allowance ──")
    result2 = clob.update_balance_allowance(params)
    print(f"update_balance_allowance: {json.dumps(result2, indent=2)}")

    print("\n── CLOB get_balance_allowance (after update) ──")
    result3 = clob.get_balance_allowance(params)
    print(f"get_balance_allowance: {json.dumps(result3, indent=2)}")

if __name__ == "__main__":
    main()
