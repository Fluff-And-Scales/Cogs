from __future__ import annotations

import contextlib

import discord
from redbot.core import commands


class CCSendDM(commands.Cog):
	"""Send direct messages to specific users via bot command."""

	_NOTICE_DELETE_SECONDS = 10

	def __init__(self, bot: commands.Bot):
		self.bot = bot

	async def _cleanup_invocation(self, ctx: commands.Context) -> None:
		with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
			await ctx.message.delete()

	async def _send_temporary_notice(self, ctx: commands.Context, text: str) -> None:
		with contextlib.suppress(discord.Forbidden, discord.HTTPException):
			msg = await ctx.send(text)
			with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
				await msg.delete(delay=self._NOTICE_DELETE_SECONDS)

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
			await self._send_temporary_notice(ctx, "Message content cannot be empty.")
			await self._cleanup_invocation(ctx)
			return

		try:
			await user.send(content)
		except discord.Forbidden:
			await self._send_temporary_notice(ctx, "I could not DM that user (DMs may be disabled).")
			await self._cleanup_invocation(ctx)
			return
		except discord.HTTPException:
			await self._send_temporary_notice(ctx, "Failed to send DM due to a Discord API error.")
			await self._cleanup_invocation(ctx)
			return

		with contextlib.suppress(discord.Forbidden, discord.HTTPException):
			await ctx.message.add_reaction("✅")
		await self._cleanup_invocation(ctx)

	@ccsenddm.command(name="sendas")
	async def ccsenddm_sendas(self, ctx: commands.Context, user: discord.User, *, message: str) -> None:
		"""Send a DM with sender context prefix."""
		content = message.strip()
		if not content:
			await self._send_temporary_notice(ctx, "Message content cannot be empty.")
			await self._cleanup_invocation(ctx)
			return

		sender = ctx.author.display_name if isinstance(ctx.author, discord.Member) else str(ctx.author)
		payload = f"Message from {sender}:\n{content}"

		try:
			await user.send(payload)
		except discord.Forbidden:
			await self._send_temporary_notice(ctx, "I could not DM that user (DMs may be disabled).")
			await self._cleanup_invocation(ctx)
			return
		except discord.HTTPException:
			await self._send_temporary_notice(ctx, "Failed to send DM due to a Discord API error.")
			await self._cleanup_invocation(ctx)
			return

		with contextlib.suppress(discord.Forbidden, discord.HTTPException):
			await ctx.message.add_reaction("✅")
		await self._cleanup_invocation(ctx)


async def setup(bot: commands.Bot):
	await bot.add_cog(CCSendDM(bot))
