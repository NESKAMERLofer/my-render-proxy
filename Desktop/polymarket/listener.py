"""
Telethon listener — читает @PolyInsiderAlerts от имени твоего аккаунта
и автоматически сохраняет трейды в базу + уведомляет твоего бота.
"""

import asyncio
import logging
import os

from telethon import TelegramClient, events
from telethon.tl.types import Channel

import config
import database
from parser import parse_message

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ─── настройки ────────────────────────────────────────────────────────────────

API_ID   = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
CHANNEL  = os.getenv("LISTEN_CHANNEL", "PolyInsiderAlerts")

# ─── запуск ───────────────────────────────────────────────────────────────────

async def main():
    database.init_db()
    logger.info("Connecting to Telegram as user...")

    client = TelegramClient(
        "polymarket_session", API_ID, API_HASH,
        device_model="Desktop",
        system_version="Windows 10",
        app_version="4.16.6",
        flood_sleep_threshold=60,   # авто-пауза при flood wait
    )
    await client.start()

    me = await client.get_me()
    logger.info("Logged in as: %s (@%s)", me.first_name, me.username)

    # Подписка на новые сообщения канала
    @client.on(events.NewMessage(chats=CHANNEL))
    async def handler(event):
        text = event.message.message
        if not text:
            return

        signal = parse_message(text)
        if signal is None:
            return  # не трейд-сообщение

        trade_id = database.insert_trade(signal)
        emoji = "🟢" if signal.action == "BUY" else "🔴"
        logger.info(
            "✅ Saved #%d — %s %s @ %.0f¢ $%.0f  wallet=%s",
            trade_id, signal.action, signal.asset,
            signal.price_cents, signal.amount_usd, signal.wallet,
        )

        # Уведомить твоего бота (если ADMIN_CHAT_ID задан)
        if config.ADMIN_CHAT_ID:
            try:
                from telegram import Bot
                bot = Bot(token=config.BOT_TOKEN)
                await bot.send_message(
                    chat_id=config.ADMIN_CHAT_ID,
                    text=(
                        f"✅ #{trade_id} сохранён\n"
                        f"{emoji} {signal.action} *{signal.asset}* @ {signal.price_cents:.0f}¢ — "
                        f"${signal.amount_usd:,.0f}\n"
                        f"👤 `{signal.wallet}`"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning("Не удалось уведомить бота: %s", e)

    logger.info("👂 Слушаю @%s ...", CHANNEL)
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
