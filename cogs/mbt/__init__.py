from .cog import BillsCog


async def setup(bot) -> None:
    await bot.add_cog(BillsCog(bot))
