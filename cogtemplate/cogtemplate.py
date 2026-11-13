from __future__ import annotations

import discord
from redbot.core import Config, commands


class CogTemplate(commands.Cog):
	"""Starter template cog for creating new Redbot cogs."""

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.config = Config.get_conf(self, identifier=853217649999, force_registration=True)
		self.config.register_guild(enabled=True)
		self.config.register_user(example_value="")

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	async def template(self, ctx: commands.Context) -> None:
		"""Example user-facing command group."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@template.command(name="ping")
	async def template_ping(self, ctx: commands.Context) -> None:
		"""Example command."""
		await ctx.send("Pong from CogTemplate.")

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	@commands.admin_or_permissions(manage_guild=True)
	async def templateset(self, ctx: commands.Context) -> None:
		"""Example settings command group."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@templateset.command(name="enable")
	async def templateset_enable(self, ctx: commands.Context) -> None:
		"""Enable this cog's behavior in the current server."""
		await self.config.guild(ctx.guild).enabled.set(True)
		await ctx.send("Template behavior enabled.")

	@templateset.command(name="disable")
	async def templateset_disable(self, ctx: commands.Context) -> None:
		"""Disable this cog's behavior in the current server."""
		await self.config.guild(ctx.guild).enabled.set(False)
		await ctx.send("Template behavior disabled.")

	@templateset.command(name="show")
	async def templateset_show(self, ctx: commands.Context) -> None:
		"""Show current template settings."""
		enabled = await self.config.guild(ctx.guild).enabled()
		await ctx.send(f"Template enabled: `{enabled}`")

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message) -> None:
		"""Example listener skeleton."""
		if message.guild is None or message.author.bot:
			return
		enabled = await self.config.guild(message.guild).enabled()
		if not enabled:
			return
		# Add listener logic here.


async def setup(bot: commands.Bot):
	await bot.add_cog(CogTemplate(bot))
