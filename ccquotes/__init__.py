from .ccquotes import CCQuotes


async def setup(bot):
	await bot.add_cog(CCQuotes(bot))
