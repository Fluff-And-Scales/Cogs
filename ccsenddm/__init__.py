from .ccsenddm import CCSendDM


async def setup(bot):
	await bot.add_cog(CCSendDM(bot))
