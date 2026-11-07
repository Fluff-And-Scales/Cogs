from .ccautoclean import CCAutoClean


async def setup(bot):
	await bot.add_cog(CCAutoClean(bot))
