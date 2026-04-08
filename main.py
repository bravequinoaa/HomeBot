import asyncio
import logging
import logging.handlers
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------------
# Configurable paths — override via env vars in production.
# Defaults keep local dev working without Docker.
#   DATA_DIR  → where bills.json, calendar.json, ics_exports/ live
#   LOGS_DIR  → where storage.log, claude_responses.log live
# In docker-compose these are set to /apps/homebot/data and /apps/homebot/logs
# ------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
LOGS_DIR = Path(os.environ.get("LOGS_DIR", "logs"))

# ------------------------------------------------------------------
# Root logger — INFO+ to stdout for all homebot.* loggers
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("homebot")

# ------------------------------------------------------------------
# Main log file — INFO+ from all homebot.* loggers to LOGS_DIR/homebot.log
# Rotates at 10 MB, keeps 5 backups.
# ------------------------------------------------------------------
_main_fh = logging.handlers.RotatingFileHandler(
    LOGS_DIR / "homebot.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_main_fh.setLevel(logging.INFO)
_main_fh.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
log.addHandler(_main_fh)

# ------------------------------------------------------------------
# Storage logger — DEBUG+ to LOGS_DIR/storage.log
# INFO+ propagates to the root handler (main console/log) automatically.
# ------------------------------------------------------------------
_storage_log = logging.getLogger("homebot.storage")
_storage_log.setLevel(logging.DEBUG)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
_storage_fh = logging.FileHandler(LOGS_DIR / "storage.log", encoding="utf-8")
_storage_fh.setLevel(logging.DEBUG)
_storage_fh.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s [%(funcName)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
_storage_log.addHandler(_storage_fh)

# Add new cog module paths here to extend the bot
COGS = [
    "cogs.console",
    "cogs.schedule",
    "cogs.alerts",
    "cogs.mbt",
]


async def main() -> None:
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix="!", intents=intents)

    # ------------------------------------------------------------------
    # Storage factory + manager — registered before cogs load so each
    # cog can call bot.storage_manager.get("<name>") in __init__.
    # ------------------------------------------------------------------
    from util.storage import StorageFactory, StorageManager
    from cogs.mbt.storage import BillsStorage
    from cogs.alerts.storage import CalendarStorage

    factory = StorageFactory()
    factory.register(
        "bills",
        BillsStorage,
        path=DATA_DIR / "bills.json",
        collection_key="bills",
    )
    factory.register(
        "calendar",
        CalendarStorage,
        path=DATA_DIR / "calendar.json",
        collection_key="events",
    )
    bot.storage_manager = StorageManager(factory)
    bot.data_dir = DATA_DIR  # expose for cogs that need the base path
    log.info(
        "Storage manager initialised — DATA_DIR=%s LOGS_DIR=%s",
        DATA_DIR, LOGS_DIR,
    )

    @bot.event
    async def on_ready() -> None:
        log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)

    for cog in COGS:
        await bot.load_extension(cog)
        log.info("Loaded cog: %s", cog)

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set in the environment.")

    await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
