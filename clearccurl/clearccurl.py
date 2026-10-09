from __future__ import annotations

import contextlib
import re

import discord
from redbot.core import commands


class ClearCCUrl(commands.Cog):
	"""Convert markdown image links in bot output into embeds without URL text."""

	_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
	_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg")

	def __init__(self, bot: commands.Bot):
		self.bot = bot

	def _is_image_url(self, value: str) -> bool:
		target = value.strip().strip("<>")
		target_lower = target.casefold()
		if not (target_lower.startswith("http://") or target_lower.startswith("https://")):
			return False
		path = target_lower.split("?", 1)[0]
		return path.endswith(self._IMAGE_EXTENSIONS)

	def _sanitize_content(self, content: str) -> tuple[str, list[str]]:
		image_urls: list[str] = []

		def _replace(match: re.Match[str]) -> str:
			alt_text = match.group(1).strip()
			target_raw = match.group(2).strip()
			target = target_raw.strip("<>")

			if self._is_image_url(target_raw):
				image_urls.append(target)
				return alt_text

			return alt_text or target

		cleaned = self._MARKDOWN_IMAGE_RE.sub(_replace, content)
		cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
		cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
		return cleaned.strip(), image_urls

	def _build_embeds(self, image_urls: list[str]) -> list[discord.Embed]:
		embeds: list[discord.Embed] = []
		for url in image_urls[:10]:
			embed = discord.Embed()
			embed.set_image(url=url)
			embeds.append(embed)
		return embeds

	async def _rewrite_message(self, message: discord.Message, updated_content: str, embeds: list[discord.Embed]) -> None:
		content_payload = updated_content or None
		try:
			await message.edit(content=content_payload, embeds=embeds)
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
			await message.channel.send(content=content_payload, embeds=embeds)

		if permissions.manage_messages:
			with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
				await message.delete()

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message) -> None:
		if message.guild is None or not message.author.bot:
			return

		if not message.content or "![" not in message.content:
			return

		updated_content, image_urls = self._sanitize_content(message.content)
		if updated_content == message.content and not image_urls:
			return

		embeds = self._build_embeds(image_urls)
		await self._rewrite_message(message, updated_content, embeds)


async def setup(bot: commands.Bot):
	await bot.add_cog(ClearCCUrl(bot))
