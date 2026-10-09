from .cclistref import CCListRef


async def setup(bot):
	await bot.add_cog(CCListRef(bot))
