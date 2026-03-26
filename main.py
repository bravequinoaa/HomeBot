import asyncio
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

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
# Storage logger — DEBUG+ to logs/storage.log; INFO+ propagates up
# to the root handler so high-level storage events still appear in
# the main console/log without the verbose DEBUG noise.
# ------------------------------------------------------------------
_storage_log = logging.getLogger("homebot.storage")
_storage_log.setLevel(logging.DEBUG)
Path("logs").mkdir(exist_ok=True)
_storage_fh = logging.FileHandler("logs/storage.log", encoding="utf-8")
_storage_fh.setLevel(logging.DEBUG)
_storage_fh.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s [%(funcName)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
_storage_log.addHandler(_storage_fh)
# propagate=True (default) means INFO+ still reaches the root console handler

# Add new cog module paths here to extend the bot
COGS = [
    "cogs.schedule",
    "cogs.alerts",
    "cogs.bills",
]


async def main() -> None:
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix="!", intents=intents)

    # ------------------------------------------------------------------
    # Storage factory + manager — registered before cogs are loaded so
    # each cog can call bot.storage_manager.get("<name>") in __init__.
    # ------------------------------------------------------------------
    from util.storage import StorageFactory, StorageManager
    from cogs.bills.storage import BillsStorage
    from cogs.alerts.storage import CalendarStorage

    factory = StorageFactory()
    factory.register(
        "bills",
        BillsStorage,
        path=Path("data/bills.json"),
        collection_key="bills",
    )
    factory.register(
        "calendar",
        CalendarStorage,
        path=Path("data/calendar.json"),
        collection_key="events",
    )
    bot.storage_manager = StorageManager(factory)
    log.info("Storage manager initialised with %d registered store(s)", 2)

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
