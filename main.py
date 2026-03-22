import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("homebot")

# Add new cog module paths here to extend the bot
COGS = [
    "cogs.schedule",
]


async def main() -> None:
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix="!", intents=intents)

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
