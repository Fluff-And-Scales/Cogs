from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any

import discord
from redbot.core import Config, commands


class CCAutoClean(commands.Cog):
	"""Automatically delete messages in configured channels after per-channel delays."""

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.config = Config.get_conf(self, identifier=853217640114, force_registration=True)
		self.config.register_guild(enabled=True, channel_delays={})

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	@commands.admin_or_permissions(manage_messages=True)
	async def ccautocleanset(self, ctx: commands.Context) -> None:
		"""Configure per-channel auto-delete cleanup settings."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@ccautocleanset.command(name="enable")
	async def ccautocleanset_enable(self, ctx: commands.Context) -> None:
		"""Enable auto-cleaning for this server."""
		await self.config.guild(ctx.guild).enabled.set(True)
		await ctx.send("CCAutoClean enabled.")

	@ccautocleanset.command(name="disable")
	async def ccautocleanset_disable(self, ctx: commands.Context) -> None:
		"""Disable auto-cleaning for this server."""
		await self.config.guild(ctx.guild).enabled.set(False)
		await ctx.send("CCAutoClean disabled.")

	@ccautocleanset.command(name="set")
	async def ccautocleanset_set(self, ctx: commands.Context, channel: discord.TextChannel, seconds: int) -> None:
		"""Set auto-delete delay for a channel."""
		seconds = max(0, int(seconds))
		if seconds == 0:
			await ctx.send("Seconds must be greater than 0. Use `ccautocleanset remove <channel>` to disable a channel.")
			return

		async with self.config.guild(ctx.guild).channel_delays() as channel_delays:
			channel_delays[str(channel.id)] = seconds

		await ctx.send(f"Auto-clean set for {channel.mention}: messages delete after {seconds}s.")

	@ccautocleanset.command(name="remove")
	async def ccautocleanset_remove(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
		"""Remove auto-delete for a channel."""
		removed = False
		async with self.config.guild(ctx.guild).channel_delays() as channel_delays:
			if str(channel.id) in channel_delays:
				channel_delays.pop(str(channel.id), None)
				removed = True

		if removed:
			await ctx.send(f"Removed auto-clean for {channel.mention}.")
		else:
			await ctx.send(f"{channel.mention} was not configured.")

	@ccautocleanset.command(name="list")
	async def ccautocleanset_list(self, ctx: commands.Context) -> None:
		"""List current per-channel auto-delete settings."""
		conf = await self.config.guild(ctx.guild).all()
		enabled = conf.get("enabled", True)
		channel_delays: dict[str, Any] = conf.get("channel_delays", {})

		if not channel_delays:
			await ctx.send(f"CCAutoClean enabled: `{enabled}`\nNo channels configured.")
			return

		lines = [f"CCAutoClean enabled: `{enabled}`", "Configured channels:"]
		for channel_id_str, seconds in sorted(channel_delays.items(), key=lambda kv: int(kv[0])):
			channel_obj = None
			with contextlib.suppress(ValueError):
				channel_obj = ctx.guild.get_channel(int(channel_id_str))
			channel_display = channel_obj.mention if isinstance(channel_obj, discord.TextChannel) else f"#{channel_id_str}"
			lines.append(f"- {channel_display}: {int(seconds)}s")

		await ctx.send("\n".join(lines))

	@ccautocleanset.command(name="clear")
	async def ccautocleanset_clear(self, ctx: commands.Context) -> None:
		"""Clear all configured channel cleanup rules in this server."""
		await self.config.guild(ctx.guild).channel_delays.set({})
		await ctx.send("Cleared all CCAutoClean channel settings.")

	@ccautocleanset.command(name="backfill")
	async def ccautocleanset_backfill(
		self,
		ctx: commands.Context,
		channel: discord.TextChannel,
		max_messages: int = 1000,
	) -> None:
		"""Delete existing non-bot messages older than this channel's configured delay."""
		channel_delays = await self.config.guild(ctx.guild).channel_delays()
		delay_value = channel_delays.get(str(channel.id))
		if delay_value is None:
			await ctx.send(f"{channel.mention} has no configured delay. Use `ccautocleanset set {channel.mention} <seconds>` first.")
			return

		try:
			delay_seconds = max(1, int(delay_value))
		except (TypeError, ValueError):
			await ctx.send("Configured delay for that channel is invalid.")
			return

		max_messages = max(1, min(10000, int(max_messages)))
		await ctx.send(
			f"Backfill cleanup started for {channel.mention}: deleting non-bot messages older than {delay_seconds}s "
			f"(scan limit: {max_messages})."
		)
		deleted, scanned = await self._purge_existing_messages(channel, delay_seconds, max_messages)
		await ctx.send(
			f"Backfill cleanup finished for {channel.mention}. Scanned: {scanned}, deleted: {deleted}."
		)

	async def _purge_existing_messages(
		self,
		channel: discord.TextChannel,
		older_than_seconds: int,
		max_messages: int,
	) -> tuple[int, int]:
		cutoff = discord.utils.utcnow() - timedelta(seconds=older_than_seconds)
		deleted = 0
		scanned = 0
		async for message in channel.history(limit=max_messages, oldest_first=False):
			scanned += 1
			if message.author.bot:
				continue
			if message.pinned:
				continue
			if message.created_at > cutoff:
				continue
			with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
				await message.delete()
				deleted += 1
		return deleted, scanned

	async def _delete_later(self, message: discord.Message, delay_seconds: int) -> None:
		await asyncio.sleep(delay_seconds)
		with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
			await message.delete()

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message) -> None:
		if message.guild is None:
			return
		if message.author.bot:
			return

		guild_conf = self.config.guild(message.guild)
		if not await guild_conf.enabled():
			return

		channel_delays = await guild_conf.channel_delays()
		delay = channel_delays.get(str(message.channel.id))
		if delay is None:
			return

		try:
			delay_seconds = max(1, int(delay))
		except (TypeError, ValueError):
			return

		self.bot.loop.create_task(self._delete_later(message, delay_seconds))


async def setup(bot: commands.Bot):
	await bot.add_cog(CCAutoClean(bot))
