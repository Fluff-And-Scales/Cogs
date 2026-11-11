from .ccwordguess import CCWordGuess


async def setup(bot):
	await bot.add_cog(CCWordGuess(bot))
