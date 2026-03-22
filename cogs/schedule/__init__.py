from .cog import ScheduleCog


async def setup(bot) -> None:
    await bot.add_cog(ScheduleCog(bot))
