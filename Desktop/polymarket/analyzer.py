"""
Risk management and PnL analysis per wallet and globally.
"""

from typing import Dict, Any, List
import database


def _risk_score(stats: Dict) -> str:
    """
    Simple risk score based on position sizing and activity.
    Returns: Low / Medium / High / Very High
    """
    if stats['total_trades'] == 0:
        return "—"
    avg_size = stats['buy_volume'] / max(stats['buy_count'], 1)
    if avg_size > 50_000:
        return "🔴 Very High"
    if avg_size > 20_000:
        return "🟠 High"
    if avg_size > 5_000:
        return "🟡 Medium"
    return "🟢 Low"


def wallet_analysis(wallet: str) -> Dict[str, Any]:
    stats = database.get_wallet_stats(wallet)
    if not stats:
        return {}

    trades = database.get_wallet_trades(wallet)
    buy_prices = [t['price_cents'] for t in trades if t['action'] == 'BUY' and t['price_cents'] > 0]
    sell_prices = [t['price_cents'] for t in trades if t['action'] == 'SELL' and t['price_cents'] > 0]

    avg_buy_price = sum(buy_prices) / len(buy_prices) if buy_prices else 0
    avg_sell_price = sum(sell_prices) / len(sell_prices) if sell_prices else 0

    buy_count = stats['buy_count']
    sell_count = stats['sell_count']
    total = stats['total_trades']

    # Sell rate — how often they close positions
    sell_rate = round(sell_count / buy_count * 100, 1) if buy_count > 0 else 0

    # Approximate win rate based on sell price > buy price
    win_rate = None
    if avg_buy_price > 0 and avg_sell_price > 0:
        win_rate = round((avg_sell_price / avg_buy_price - 1) * 100, 1)

    # Average position size
    avg_buy_size = stats['buy_volume'] / buy_count if buy_count > 0 else 0
    max_trade = max((t['amount_usd'] for t in trades), default=0)

    # Open exposure (buy_volume - sell_volume, approximate)
    open_exposure = stats['buy_volume'] - stats['sell_volume']

    # Markets traded
    markets = list({t['market'] for t in trades if t['market']})

    # Concentration: single market risk
    from collections import Counter
    market_counts = Counter(t['market'] for t in trades if t['market'])
    top_market = market_counts.most_common(1)[0] if market_counts else None
    concentration = round(top_market[1] / total * 100, 1) if top_market else 0

    return {
        "wallet": wallet,
        "first_seen": stats['first_seen'],
        "last_seen": stats['last_seen'],
        "total_trades": total,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_volume": stats['buy_volume'],
        "sell_volume": stats['sell_volume'],
        "realized_pnl": stats['realized_pnl'],
        "open_exposure": open_exposure,
        "avg_buy_price_cents": round(avg_buy_price, 1),
        "avg_sell_price_cents": round(avg_sell_price, 1),
        "avg_buy_size": round(avg_buy_size, 0),
        "max_trade": max_trade,
        "sell_rate_pct": sell_rate,
        "price_gain_pct": win_rate,
        "markets_count": len(markets),
        "top_market": top_market[0] if top_market else "—",
        "concentration_pct": concentration,
        "risk_score": _risk_score(stats),
    }


def global_analysis() -> Dict[str, Any]:
    stats = database.get_global_stats()
    top_by_pnl = database.get_top_wallets(by="realized_pnl", limit=5)
    top_by_volume = database.get_top_wallets(by="buy_volume", limit=5)
    top_by_trades = database.get_top_wallets(by="total_trades", limit=5)
    return {
        **stats,
        "top_by_pnl": top_by_pnl,
        "top_by_volume": top_by_volume,
        "top_by_trades": top_by_trades,
    }


def format_wallet_report(wallet: str) -> str:
    a = wallet_analysis(wallet)
    if not a:
        return f"❌ Wallet `{wallet}` not found in database."

    pnl_sign = "+" if a['realized_pnl'] >= 0 else ""
    price_gain = f"{a['price_gain_pct']:+.1f}%" if a['price_gain_pct'] is not None else "N/A"
    exposure = f"${a['open_exposure']:,.0f}" if a['open_exposure'] > 0 else "$0"

    return (
        f"👤 *Wallet:* `{a['wallet']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 First seen: {a['first_seen'][:10]}\n"
        f"📅 Last active: {a['last_seen'][:10]}\n\n"
        f"📊 *Activity*\n"
        f"  Trades: {a['total_trades']} (🟢{a['buy_count']} BUY / 🔴{a['sell_count']} SELL)\n"
        f"  Sell rate: {a['sell_rate_pct']}%\n"
        f"  Markets: {a['markets_count']}\n\n"
        f"💰 *Volume & PnL*\n"
        f"  Buy volume:  ${a['buy_volume']:>12,.0f}\n"
        f"  Sell volume: ${a['sell_volume']:>12,.0f}\n"
        f"  Realized PnL: {pnl_sign}${a['realized_pnl']:,.0f}\n"
        f"  Open exposure: {exposure}\n\n"
        f"📈 *Prices*\n"
        f"  Avg buy:  {a['avg_buy_price_cents']:.1f}¢\n"
        f"  Avg sell: {a['avg_sell_price_cents']:.1f}¢\n"
        f"  Price gain: {price_gain}\n\n"
        f"⚖️ *Risk Management*\n"
        f"  Risk score: {a['risk_score']}\n"
        f"  Avg position: ${a['avg_buy_size']:,.0f}\n"
        f"  Largest trade: ${a['max_trade']:,.0f}\n"
        f"  Top market: {a['top_market'][:40] if a['top_market'] else '—'}\n"
        f"  Concentration: {a['concentration_pct']}% in top market\n"
    )


def format_global_report() -> str:
    a = global_analysis()
    lines = [
        "🌍 *Global Polymarket Insider Stats*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Total trades tracked: {a['total_trades']}",
        f"👥 Unique wallets: {a['total_wallets']}",
        f"💰 Total volume: ${a['total_volume']:,.0f}",
        f"  🟢 Buy:  ${a['buy_volume']:,.0f}",
        f"  🔴 Sell: ${a['sell_volume']:,.0f}",
        "",
    ]
    if a['top_market']:
        lines.append(f"🏆 Hottest market: {a['top_market']['market'][:50]}")
        lines.append("")

    if a['top_by_pnl']:
        lines.append("🏅 *Top 5 by PnL:*")
        for i, w in enumerate(a['top_by_pnl'], 1):
            sign = "+" if w['realized_pnl'] >= 0 else ""
            lines.append(f"  {i}. `{w['wallet']}` — {sign}${w['realized_pnl']:,.0f}")
        lines.append("")

    if a['top_by_volume']:
        lines.append("💸 *Top 5 by Volume:*")
        for i, w in enumerate(a['top_by_volume'], 1):
            lines.append(f"  {i}. `{w['wallet']}` — ${w['buy_volume']:,.0f}")
        lines.append("")

    return "\n".join(lines)


def format_top_report(limit: int = 10) -> str:
    wallets = database.get_top_wallets(by="realized_pnl", limit=limit)
    if not wallets:
        return "📭 No data yet."
    lines = [f"🏆 *Top {limit} Traders by PnL*", "━━━━━━━━━━━━━━━━━━━━━"]
    for i, w in enumerate(wallets, 1):
        sign = "+" if w['realized_pnl'] >= 0 else ""
        lines.append(
            f"{i}. `{w['wallet']}`\n"
            f"   PnL: {sign}${w['realized_pnl']:,.0f} | "
            f"Vol: ${w['buy_volume']:,.0f} | "
            f"Trades: {w['total_trades']}"
        )
    return "\n".join(lines)
