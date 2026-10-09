from __future__ import annotations

import contextlib
import re

import discord
from redbot.core import commands


class ClearCCUrl(commands.Cog):
	"""Convert markdown image links in bot output to plain URLs."""

	_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", re.IGNORECASE)

	def __init__(self, bot: commands.Bot):
		self.bot = bot

	def _sanitize_content(self, content: str) -> str:
		return self._MARKDOWN_IMAGE_RE.sub(r"\1", content)

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message) -> None:
		if message.guild is None:
			return

		if self.bot.user is None or message.author.id != self.bot.user.id:
			return

		if "![" not in message.content:
			return

		updated_content = self._sanitize_content(message.content)
		if updated_content == message.content:
			return

		with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
			await message.edit(content=updated_content)


async def setup(bot: commands.Bot):
	await bot.add_cog(ClearCCUrl(bot))
