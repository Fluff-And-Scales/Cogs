from __future__ import annotations

import contextlib
import re

import discord
from redbot.core import commands


class ClearCCUrl(commands.Cog):
	"""Convert markdown image links in bot output to plain URLs."""

	_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(<?(https?://[^)\s>]+)>?\)", re.IGNORECASE)

	def __init__(self, bot: commands.Bot):
		self.bot = bot

	def _sanitize_content(self, content: str) -> str:
		return self._MARKDOWN_IMAGE_RE.sub(r"\1", content)

	async def _rewrite_message(self, message: discord.Message, updated_content: str) -> None:
		try:
			await message.edit(content=updated_content)
			return
		except (discord.Forbidden, discord.NotFound, discord.HTTPException):
			pass

		me = message.guild.me
		if me is None:
			return

		permissions = message.channel.permissions_for(me)
		if not permissions.send_messages:
			return

		with contextlib.suppress(discord.Forbidden, discord.HTTPException):
			await message.channel.send(updated_content)

		if permissions.manage_messages:
			with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
				await message.delete()

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message) -> None:
		if message.guild is None or not message.author.bot:
			return

		if not message.content or "![" not in message.content:
			return

		updated_content = self._sanitize_content(message.content)
		if updated_content == message.content:
			return

		await self._rewrite_message(message, updated_content)


async def setup(bot: commands.Bot):
	await bot.add_cog(ClearCCUrl(bot))
