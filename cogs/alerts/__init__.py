from .cog import AlertsCog


async def setup(bot) -> None:
    await bot.add_cog(AlertsCog(bot))
