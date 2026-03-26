from .cog import ConsoleCog


async def setup(bot) -> None:
    await bot.add_cog(ConsoleCog(bot))
