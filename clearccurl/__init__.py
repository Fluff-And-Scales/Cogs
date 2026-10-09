from .clearccurl import ClearCCUrl


async def setup(bot):
	await bot.add_cog(ClearCCUrl(bot))
