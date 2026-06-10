"""
Telegram bot — receives forwarded PMTrackBot messages and answers commands.

Commands:
  /start        — welcome
  /stats        — global stats
  /top [N]      — top N traders by PnL (default 10)
  /wallet <addr> — full analysis of a wallet
  /recent [N]   — last N trades (default 10)
  /report       — full global report
  /help         — command list
"""

import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
import database
import analyzer
from parser import parse_message

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


# ─── helpers ──────────────────────────────────────────────────────────────────

async def _is_authorized(update: Update) -> bool:
    """Only admin chat can use commands (set ADMIN_CHAT_ID=0 to allow all)."""
    if config.ADMIN_CHAT_ID == 0:
        return True
    return update.effective_chat.id == config.ADMIN_CHAT_ID


# ─── command handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_authorized(update):
        return
    chat_id = update.effective_chat.id
    logger.info("💬 /start from chat_id=%s", chat_id)
    await update.message.reply_text(
        f"🆔 Your chat\\_id: `{chat_id}`\n\n",
        parse_mode="Markdown",
    )
    await update.message.reply_text(
        "👋 *Polymarket Insider Tracker*\n\n"
        "Forward me messages from @PMTrackBot and I'll track and analyze them.\n\n"
        "Commands:\n"
        "/stats — global overview\n"
        "/top [N] — top traders by PnL\n"
        "/wallet <addr> — wallet analysis\n"
        "/recent [N] — last N trades\n"
        "/report — full report\n"
        "/help — this message",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_authorized(update):
        return
    text = analyzer.format_global_report()
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_authorized(update):
        return
    args = ctx.args
    limit = 10
    if args:
        try:
            limit = max(1, min(int(args[0]), 50))
        except ValueError:
            pass
    text = analyzer.format_top_report(limit=limit)
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_authorized(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /wallet <address>")
        return
    wallet = ctx.args[0].strip()
    text = analyzer.format_wallet_report(wallet)
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_recent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_authorized(update):
        return
    limit = 10
    if ctx.args:
        try:
            limit = max(1, min(int(ctx.args[0]), 50))
        except ValueError:
            pass
    trades = database.get_recent_trades(limit=limit)
    if not trades:
        await update.message.reply_text("📭 No trades yet.")
        return
    lines = [f"📋 *Last {len(trades)} Trades*", "━━━━━━━━━━━━━━━━━━━━━"]
    for t in trades:
        emoji = "🟢" if t['action'] == 'BUY' else "🔴"
        lines.append(
            f"{emoji} {t['action']} {t['asset']} @ {t['price_cents']:.0f}¢ "
            f"| ${t['amount_usd']:,.0f}\n"
            f"   `{t['wallet']}` — {t['ts'][:10]}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_authorized(update):
        return
    global_text = analyzer.format_global_report()
    top_text = analyzer.format_top_report(limit=10)
    await update.message.reply_text(global_text, parse_mode="Markdown")
    await update.message.reply_text(top_text, parse_mode="Markdown")


# ─── message handler (incoming PMTrackBot data) ───────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_authorized(update):
        return

    msg = update.message
    if msg is None:
        return

    text = msg.text or msg.caption or ""
    if not text:
        return

    signal = parse_message(text)
    if signal is None:
        # Not a PMTrackBot trade message — ignore silently
        return

    trade_id = database.insert_trade(signal)
    emoji = "🟢" if signal.action == "BUY" else "🔴"
    logger.info(
        "Saved trade #%d: %s %s %s @ %.0f¢ $%.0f wallet=%s",
        trade_id, signal.action, signal.asset, signal.market,
        signal.price_cents, signal.amount_usd, signal.wallet,
    )

    await msg.reply_text(
        f"✅ Saved #{trade_id}\n"
        f"{emoji} {signal.action} *{signal.asset}* @ {signal.price_cents:.0f}¢ — "
        f"${signal.amount_usd:,.0f}\n"
        f"👤 `{signal.wallet}`",
        parse_mode="Markdown",
    )


# ─── entry point ──────────────────────────────────────────────────────────────

def run():
    database.init_db()
    logger.info("Database initialized.")

    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("top",    cmd_top))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("recent", cmd_recent))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started. Waiting for messages...")
    app.run_polling(drop_pending_updates=True)
