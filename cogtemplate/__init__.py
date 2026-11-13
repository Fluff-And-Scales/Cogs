from .cogtemplate import CogTemplate


async def setup(bot):
	await bot.add_cog(CogTemplate(bot))
