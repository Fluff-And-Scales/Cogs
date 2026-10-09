from .clearccinvoc import ClearCCInvoc


async def setup(bot):
	await bot.add_cog(ClearCCInvoc(bot))
