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

	def _sanitize_content(self, content: str) -> str:
		def _replace(match: re.Match[str]) -> str:
			alt_text = match.group(1).strip()
			target_raw = match.group(2).strip()
			target = target_raw.strip("<>")

			if self._is_image_url(target_raw):
				return alt_text

			return alt_text or target

		cleaned = self._MARKDOWN_IMAGE_RE.sub(_replace, content)
		cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
		cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
		return cleaned.strip()

	def _sanitize_embeds(self, embeds: list[discord.Embed]) -> tuple[list[discord.Embed], bool]:
		sanitized: list[discord.Embed] = []
		has_changes = False

		for embed in embeds:
			data = embed.to_dict()
			description = data.get("description")
			embed_changed = False

			if isinstance(description, str) and "![" in description:
				cleaned_description = self._sanitize_content(description)
				if cleaned_description != description:
					embed_changed = True
					if cleaned_description:
						data["description"] = cleaned_description
					else:
						data.pop("description", None)

			has_changes = has_changes or embed_changed
			sanitized.append(discord.Embed.from_dict(data) if embed_changed else embed)

		return sanitized, has_changes

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

		updated_content = message.content
		if message.content and "![" in message.content:
			updated_content = self._sanitize_content(message.content)

		sanitized_embeds, embeds_changed = self._sanitize_embeds(list(message.embeds))

		content_changed = updated_content != message.content
		if not content_changed and not embeds_changed:
			return

		await self._rewrite_message(message, updated_content, sanitized_embeds)


async def setup(bot: commands.Bot):
	await bot.add_cog(ClearCCUrl(bot))
