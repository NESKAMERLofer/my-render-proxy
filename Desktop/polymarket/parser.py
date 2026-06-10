"""
Parser for PMTrackBot messages.

Example message:
🕵️ Possible Insider

🆕 New wallet, first big trade
🟢 BUY Top Esports @ 58¢
💰 $16,249
📊 LoL: Invictus Gaming vs Top Esports (BO3) - Esports World Cu…
👤 0x3146...c340
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeSignal:
    wallet: str
    action: str          # BUY or SELL
    asset: str
    price_cents: float   # price in cents (0-100)
    amount_usd: float
    market: str
    insider_type: str
    note: str
    raw: str


def parse_price(price_str: str) -> Optional[float]:
    """Parse price like '58¢' or '$0.58' or '0.58' → float cents 0-100."""
    price_str = price_str.strip()
    if '¢' in price_str:
        return float(price_str.replace('¢', '').strip())
    if '$' in price_str:
        val = float(price_str.replace('$', '').strip())
        return round(val * 100, 2)
    try:
        val = float(price_str)
        # if < 1, assume dollars
        return round(val * 100, 2) if val <= 1 else val
    except ValueError:
        return None


def parse_amount(amount_str: str) -> Optional[float]:
    """Parse '$16,249' → 16249.0"""
    cleaned = amount_str.replace('$', '').replace(',', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_message(text: str) -> Optional[TradeSignal]:
    """Parse a PMTrackBot message into a TradeSignal. Returns None if not recognized."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    insider_type = ""
    note = ""
    action = ""
    asset = ""
    price_cents = 0.0
    amount_usd = 0.0
    market = ""
    wallet = ""

    for line in lines:
        # Insider type header
        if '🕵️' in line or 'Insider' in line:
            insider_type = re.sub(r'[^\w\s]', '', line).strip()

        # Note (🆕 or ⚠️ etc.)
        elif line.startswith('🆕') or line.startswith('⚠️') or line.startswith('🔁'):
            note = line[2:].strip()

        # BUY line: 🟢 BUY Top Esports @ 58¢
        elif '🟢' in line or '🔴' in line:
            action = 'BUY' if '🟢' in line or 'BUY' in line else 'SELL'
            # Remove emoji and action word
            rest = re.sub(r'[🟢🔴]', '', line).strip()
            rest = re.sub(r'^(BUY|SELL)\s*', '', rest, flags=re.IGNORECASE).strip()
            # Split on @ to get asset and price
            if '@' in rest:
                parts = rest.rsplit('@', 1)
                asset = parts[0].strip()
                price_cents = parse_price(parts[1]) or 0.0
            else:
                asset = rest

        # Amount: 💰 $16,249
        elif '💰' in line:
            amount_str = line.replace('💰', '').strip()
            amount_usd = parse_amount(amount_str) or 0.0

        # Market: 📊 LoL: ...
        elif '📊' in line:
            market = line.replace('📊', '').strip()

        # Wallet: 👤 0x3146...c340
        elif '👤' in line:
            wallet = line.replace('👤', '').strip()

    if not wallet or not action or not asset:
        return None

    return TradeSignal(
        wallet=wallet,
        action=action,
        asset=asset,
        price_cents=price_cents,
        amount_usd=amount_usd,
        market=market,
        insider_type=insider_type,
        note=note,
        raw=text,
    )
