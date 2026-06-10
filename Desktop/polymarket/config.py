import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "polymarket.db")

# PMTrackBot username for filtering forwarded messages
PMTRACK_BOT_USERNAME = "PMTrackBot"
