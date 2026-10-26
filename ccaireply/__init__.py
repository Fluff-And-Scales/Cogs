from .ccaireply import CCAIReply


async def setup(bot):
	await bot.add_cog(CCAIReply(bot))
