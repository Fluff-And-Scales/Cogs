from .ccwordreact import CCWordReact


async def setup(bot):
	await bot.add_cog(CCWordReact(bot))
