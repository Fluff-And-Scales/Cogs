from __future__ import annotations

import contextlib

import discord
from redbot.core import commands


class CCSendDM(commands.Cog):
	"""Send direct messages to specific users via bot command."""

	def __init__(self, bot: commands.Bot):
		self.bot = bot

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	@commands.admin_or_permissions(manage_messages=True)
	async def ccsenddm(self, ctx: commands.Context) -> None:
		"""Send direct messages to users using the bot."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@ccsenddm.command(name="send")
	async def ccsenddm_send(self, ctx: commands.Context, user: discord.User, *, message: str) -> None:
		"""Send a DM to a specified user."""
		content = message.strip()
		if not content:
			await ctx.send("Message content cannot be empty.")
			return

		try:
			await user.send(content)
		except discord.Forbidden:
			await ctx.send("I could not DM that user (DMs may be disabled).")
			return
		except discord.HTTPException:
			await ctx.send("Failed to send DM due to a Discord API error.")
			return

		with contextlib.suppress(discord.Forbidden, discord.HTTPException):
			await ctx.message.add_reaction("✅")
		await ctx.send(f"DM sent to {user.mention}.")

	@ccsenddm.command(name="sendas")
	async def ccsenddm_sendas(self, ctx: commands.Context, user: discord.User, *, message: str) -> None:
		"""Send a DM with sender context prefix."""
		content = message.strip()
		if not content:
			await ctx.send("Message content cannot be empty.")
			return

		sender = ctx.author.display_name if isinstance(ctx.author, discord.Member) else str(ctx.author)
		payload = f"Message from {sender}:\n{content}"

		try:
			await user.send(payload)
		except discord.Forbidden:
			await ctx.send("I could not DM that user (DMs may be disabled).")
			return
		except discord.HTTPException:
			await ctx.send("Failed to send DM due to a Discord API error.")
			return

		with contextlib.suppress(discord.Forbidden, discord.HTTPException):
			await ctx.message.add_reaction("✅")
		await ctx.send(f"DM sent to {user.mention}.")


async def setup(bot: commands.Bot):
	await bot.add_cog(CCSendDM(bot))
