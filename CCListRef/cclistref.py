from __future__ import annotations

import asyncio
import contextlib
import re
import string
from typing import Any, Optional

import discord
from redbot.core import Config, commands


class _PreviewValue:
	def __str__(self) -> str:
		return "var"

	def __format__(self, format_spec: str) -> str:
		return "var"

	def __getattr__(self, name: str) -> "_PreviewValue":
		return self

	def __getitem__(self, key: Any) -> "_PreviewValue":
		return self


class _PreviewFormatter(string.Formatter):
	def get_value(self, key: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
		return _PreviewValue()


class CCListRef(commands.Cog):
	"""Maintain a posted reference list for CustomCom commands."""

	_CUSTOMCOM_COG_NAMES = ("CustomCom", "CustomCommands", "customcom")
	_REFRESH_INTERVAL = 60
	_TITLE = "Command List"
	_NOTE = "An '!' has to be in front of all commands for them to work."
	_VARIABLE_RE = re.compile(r"\{[^{}]+\}")
	_WHITESPACE_RE = re.compile(r"\s+")
	_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)", re.IGNORECASE)
	_URL_RE = re.compile(r"https?://[^\s)>]+", re.IGNORECASE)
	_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg")
	_COMMAND_CONTAINER_KEYS = ("commands", "ccs", "custom_commands", "customcom", "cmds")
	_COMMAND_FIELD_KEYS = {
		"content",
		"response",
		"text",
		"message",
		"reply",
		"output",
		"embed",
		"embeds",
		"cooldowns",
		"delete_trigger",
	}
	_NON_COMMAND_TABLE_KEYS = {
		"content",
		"response",
		"text",
		"message",
		"reply",
		"output",
		"embed",
		"embeds",
		"cooldowns",
		"delete_trigger",
		"title",
		"description",
		"fields",
		"thumbnail",
		"image",
		"author",
		"footer",
		"aliases",
		"roles",
		"reactions",
		"created_at",
		"edited_at",
		"command",
		"random",
		"case_insensitive",
	}

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.config = Config.get_conf(self, identifier=948275610234, force_registration=True)
		self.config.register_guild(channel_id=None, message_ids=[], excluded_commands=[])
		self._refresh_task: Optional[asyncio.Task] = None
		self._preview_formatter = _PreviewFormatter()

	async def cog_load(self) -> None:
		self._refresh_task = asyncio.create_task(self._refresh_loop())

	async def cog_unload(self) -> None:
		if self._refresh_task and not self._refresh_task.done():
			self._refresh_task.cancel()
			with contextlib.suppress(asyncio.CancelledError):
				await self._refresh_task

	def _get_customcom_cog(self) -> Optional[Any]:
		for name in self._CUSTOMCOM_COG_NAMES:
			cog = self.bot.get_cog(name)
			if cog is not None:
				return cog
		return None

	async def _refresh_loop(self) -> None:
		with contextlib.suppress(asyncio.TimeoutError):
			if hasattr(self.bot, "wait_until_red_ready"):
				await asyncio.wait_for(self.bot.wait_until_red_ready(), timeout=30)
			else:
				await asyncio.wait_for(self.bot.wait_until_ready(), timeout=30)

		while True:
			for guild in self.bot.guilds:
				with contextlib.suppress(Exception):
					await self.refresh_guild(guild)
			await asyncio.sleep(self._REFRESH_INTERVAL)

	async def refresh_guild(self, guild: discord.Guild) -> None:
		channel = await self.get_output_channel(guild)
		if channel is None:
			return

		rows = await self._get_custom_command_rows(guild)
		excluded = {
			name.casefold()
			for name in await self.config.guild(guild).excluded_commands()
			if isinstance(name, str) and name.strip()
		}
		rows = [row for row in rows if row["name"].casefold() not in excluded]
		specs = self._build_message_specs(rows)
		await self._publish_specs(channel, specs)

	async def get_output_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
		channel_id = await self.config.guild(guild).channel_id()
		if channel_id is None:
			return None

		channel = guild.get_channel(channel_id)
		return channel if isinstance(channel, discord.TextChannel) else None

	async def _get_custom_command_rows(self, guild: discord.Guild) -> list[dict[str, str]]:
		customcom = self._get_customcom_cog()
		if customcom is None:
			return []

		config = getattr(customcom, "config", None)
		if config is None:
			return []

		try:
			guild_data = await config.guild(guild).all()
		except Exception:
			return []

		command_table = self._find_command_table(guild_data)
		if not command_table:
			return []

		row_map: dict[str, dict[str, str]] = {}
		for command_name, command_data in sorted(command_table.items(), key=lambda item: str(item[0]).casefold()):
			if not isinstance(command_name, str):
				continue

			normalized_name = command_name.strip()
			if not normalized_name:
				continue

			output_text, image_url = self._extract_output_preview(command_data)
			row_map[normalized_name.casefold()] = {
				"name": normalized_name,
				"example": self._build_command_example(normalized_name, command_data),
				"output": output_text,
				"image_url": image_url or "",
			}

		return list(row_map.values())

	def _find_command_table(self, value: Any) -> Optional[dict[Any, Any]]:
		candidates = self._find_command_tables(value)
		if not candidates:
			return None

		return max(candidates, key=len)

	def _find_command_tables(self, value: Any) -> list[dict[Any, Any]]:
		candidates: list[dict[Any, Any]] = []
		if isinstance(value, dict):
			for key in self._COMMAND_CONTAINER_KEYS:
				candidate = value.get(key)
				if isinstance(candidate, dict) and self._mapping_looks_like_command_table(candidate):
					candidates.append(candidate)

			if self._mapping_looks_like_command_table(value):
				candidates.append(value)

			for nested in value.values():
				candidates.extend(self._find_command_tables(nested))

		elif isinstance(value, list):
			for item in value:
				candidates.extend(self._find_command_tables(item))

		unique_candidates: list[dict[Any, Any]] = []
		seen_ids: set[int] = set()
		for candidate in candidates:
			candidate_id = id(candidate)
			if candidate_id in seen_ids:
				continue
			seen_ids.add(candidate_id)
			unique_candidates.append(candidate)

		return unique_candidates

	def _mapping_looks_like_command_table(self, mapping: dict[Any, Any]) -> bool:
		if not mapping:
			return False

		normalized_keys = []
		for key in mapping:
			if not isinstance(key, str):
				return False
			normalized_keys.append(key.strip().casefold())

		if normalized_keys and all(key in self._NON_COMMAND_TABLE_KEYS for key in normalized_keys):
			return False

		valid_entries = 0
		for key, item in mapping.items():
			normalized_key = key.strip().casefold()
			if normalized_key in self._NON_COMMAND_TABLE_KEYS:
				continue
			if isinstance(item, dict) and any(field in item for field in self._COMMAND_FIELD_KEYS):
				valid_entries += 1
				continue
			if isinstance(item, (str, list, tuple)):
				valid_entries += 1

		return valid_entries > 0

	def _build_command_example(self, command_name: str, command_data: Any) -> str:
		example = f"!{command_name.strip()}"
		if self._command_uses_variable(command_data):
			example += " @<user>"
		return example

	def _command_uses_variable(self, value: Any) -> bool:
		if isinstance(value, str):
			return bool(self._VARIABLE_RE.search(value))

		if isinstance(value, dict):
			return any(self._command_uses_variable(item) for item in value.values())

		if isinstance(value, (list, tuple)):
			return any(self._command_uses_variable(item) for item in value)

		return False

	def _extract_output_preview(self, value: Any) -> tuple[str, str]:
		preview, image_url = self._flatten_preview_value(value)
		preview = self._render_preview_output(preview)
		if not preview and image_url:
			preview = "(image output)"
		return preview or "(no text output)", image_url or ""

	def _render_preview_output(self, value: str) -> str:
		rendered = value
		with contextlib.suppress(Exception):
			rendered = self._preview_formatter.vformat(value, (), {})
		rendered = self._strip_markdown_images(rendered)
		rendered = self._strip_bare_image_urls(rendered)
		rendered = self._VARIABLE_RE.sub("var", rendered)
		rendered = self._WHITESPACE_RE.sub(" ", rendered).strip(" |")
		return rendered

	def _flatten_preview_value(self, value: Any) -> tuple[str, str]:
		if isinstance(value, str):
			return value, self._extract_first_image_url(value)

		if isinstance(value, dict):
			text_parts: list[str] = []
			image_url = ""

			for key in ("content", "response", "text", "message", "reply", "output"):
				preview_text, preview_image = self._flatten_preview_value(value.get(key))
				if preview_text:
					text_parts.append(preview_text)
				if not image_url and preview_image:
					image_url = preview_image

			for key in ("embed", "embeds"):
				embed_text, embed_image = self._extract_embed_preview(value.get(key))
				if embed_text:
					text_parts.append(embed_text)
				if not image_url and embed_image:
					image_url = embed_image

			if not text_parts:
				embed_text, embed_image = self._extract_embed_text(value)
				if embed_text:
					text_parts.append(embed_text)
				if not image_url and embed_image:
					image_url = embed_image

			return " | ".join(part for part in text_parts if part), image_url

		if isinstance(value, (list, tuple)):
			text_parts: list[str] = []
			image_url = ""
			for item in value:
				preview_text, preview_image = self._flatten_preview_value(item)
				if preview_text:
					text_parts.append(preview_text)
				if not image_url and preview_image:
					image_url = preview_image
			return " | ".join(part for part in text_parts if part), image_url

		return "", ""

	def _extract_embed_preview(self, value: Any) -> tuple[str, str]:
		if isinstance(value, dict):
			return self._extract_embed_text(value)

		if isinstance(value, (list, tuple)):
			text_parts: list[str] = []
			image_url = ""
			for item in value:
				preview_text, preview_image = self._extract_embed_preview(item)
				if preview_text:
					text_parts.append(preview_text)
				if not image_url and preview_image:
					image_url = preview_image
			return " | ".join(part for part in text_parts if part), image_url

		return "", ""

	def _extract_embed_text(self, value: Any) -> tuple[str, str]:
		if not isinstance(value, dict):
			return "", ""

		parts: list[str] = []
		for key in ("title", "description"):
			candidate = value.get(key)
			if isinstance(candidate, str) and candidate.strip():
				parts.append(candidate)

		fields = value.get("fields")
		if isinstance(fields, list):
			for field in fields:
				if not isinstance(field, dict):
					continue
				name = field.get("name")
				field_value = field.get("value")
				if isinstance(name, str) and name.strip():
					parts.append(name)
				if isinstance(field_value, str) and field_value.strip():
					parts.append(field_value)

		image_url = self._extract_embed_image_url(value)
		return " | ".join(part for part in parts if part), image_url

	def _extract_embed_image_url(self, value: dict[str, Any]) -> str:
		for key in ("image", "thumbnail"):
			candidate = value.get(key)
			if isinstance(candidate, dict):
				url = candidate.get("url")
				if isinstance(url, str) and self._is_image_url(url):
					return url.strip().strip("<>")
		return ""

	def _extract_first_image_url(self, value: str) -> str:
		for match in self._MARKDOWN_IMAGE_RE.finditer(value):
			url = match.group(2).strip()
			if self._is_image_url(url):
				return url.strip().strip("<>")

		for match in self._URL_RE.finditer(value):
			url = match.group(0).strip()
			if self._is_image_url(url):
				return url.strip().strip("<>")

		return ""

	def _strip_markdown_images(self, value: str) -> str:
		def _replace(match: re.Match[str]) -> str:
			alt_text = match.group(1).strip()
			return alt_text

		return self._MARKDOWN_IMAGE_RE.sub(_replace, value)

	def _strip_bare_image_urls(self, value: str) -> str:
		def _replace(match: re.Match[str]) -> str:
			url = match.group(0)
			return "" if self._is_image_url(url) else url

		return self._URL_RE.sub(_replace, value)

	def _is_image_url(self, value: str) -> bool:
		target = value.strip().strip("<>")
		target_lower = target.casefold()
		if not (target_lower.startswith("http://") or target_lower.startswith("https://")):
			return False
		path = target_lower.split("?", 1)[0]
		return path.endswith(self._IMAGE_EXTENSIONS)

	def _build_message_specs(self, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
		header_embed = discord.Embed(title=self._TITLE, description=self._NOTE)
		specs: list[dict[str, Any]] = [{"content": None, "embed": header_embed}]

		if not rows:
			empty_embed = discord.Embed(title="No Custom Commands", description="No custom commands available.")
			specs.append({"content": None, "embed": empty_embed})
			return specs

		for row in rows:
			embed = discord.Embed(title=row["name"], description=row["output"])
			embed.add_field(name="Example", value=row["example"], inline=False)
			if row["image_url"]:
				embed.set_image(url=row["image_url"])
			specs.append({"content": None, "embed": embed})

		return specs

	async def _publish_specs(self, channel: discord.TextChannel, specs: list[dict[str, Any]]) -> None:
		me = channel.guild.me
		if me is None:
			return

		permissions = channel.permissions_for(me)
		if not permissions.send_messages:
			return

		stored_ids = await self.config.guild(channel.guild).message_ids()
		existing_messages: list[discord.Message] = []
		for message_id in stored_ids:
			with contextlib.suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
				existing_messages.append(await channel.fetch_message(message_id))

		published_ids: list[int] = []
		for index, spec in enumerate(specs):
			content = spec.get("content")
			embed = spec.get("embed")
			if index < len(existing_messages):
				message = existing_messages[index]
				message_embed = message.embeds[0].to_dict() if message.embeds else None
				spec_embed = embed.to_dict() if isinstance(embed, discord.Embed) else None
				if message.content != (content or "") or message_embed != spec_embed:
					with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
						await message.edit(content=content, embed=embed)
				published_ids.append(message.id)
				continue

			with contextlib.suppress(discord.Forbidden, discord.HTTPException):
				message = await channel.send(content=content, embed=embed)
				published_ids.append(message.id)

		for message in existing_messages[len(specs):]:
			with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
				await message.delete()

		await self.config.guild(channel.guild).message_ids.set(published_ids)

	async def _delete_managed_messages(self, channel: discord.TextChannel, message_ids: list[int]) -> None:
		for message_id in message_ids:
			with contextlib.suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
				message = await channel.fetch_message(message_id)
				await message.delete()

	async def cog_check(self, ctx: commands.Context) -> bool:
		if ctx.command is not None and ctx.command.qualified_name.startswith("cclistrefset"):
			return True

		if ctx.guild is None:
			raise commands.NoPrivateMessage()

		channel = await self.get_output_channel(ctx.guild)
		if channel is None:
			raise commands.UserFeedbackCheckFailure(
				"CCListRef is not configured yet. Use `cclistrefset channel #channel` first."
			)

		return True

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	@commands.admin_or_permissions(manage_guild=True)
	async def cclistrefset(self, ctx: commands.Context) -> None:
		"""Configure CCListRef settings."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@cclistrefset.command(name="channel")
	async def cclistrefset_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
		"""Set the channel CCListRef posts in."""
		old_channel = await self.get_output_channel(ctx.guild)
		old_message_ids = await self.config.guild(ctx.guild).message_ids()
		await self.config.guild(ctx.guild).channel_id.set(channel.id)
		await self.config.guild(ctx.guild).message_ids.set([])

		if old_channel is not None and old_channel.id != channel.id:
			await self._delete_managed_messages(old_channel, old_message_ids)

		await self.refresh_guild(ctx.guild)
		await ctx.send(f"CCListRef will post in {channel.mention}.")

	@cclistrefset.command(name="clear")
	async def cclistrefset_clear(self, ctx: commands.Context) -> None:
		"""Clear the configured output channel."""
		old_channel = await self.get_output_channel(ctx.guild)
		old_message_ids = await self.config.guild(ctx.guild).message_ids()
		if old_channel is not None:
			await self._delete_managed_messages(old_channel, old_message_ids)

		await self.config.guild(ctx.guild).channel_id.clear()
		await self.config.guild(ctx.guild).message_ids.set([])
		await ctx.send("CCListRef output channel cleared.")

	@cclistrefset.command(name="show")
	async def cclistrefset_show(self, ctx: commands.Context) -> None:
		"""Show the configured output channel."""
		channel = await self.get_output_channel(ctx.guild)
		if channel is None:
			await ctx.send("CCListRef does not have an output channel configured.")
			return

		excluded_count = len(await self.config.guild(ctx.guild).excluded_commands())
		await ctx.send(
			f"CCListRef is configured to post in {channel.mention}. Excluded commands: {excluded_count}."
		)

	@cclistrefset.group(name="exclude", invoke_without_command=True)
	async def cclistrefset_exclude(self, ctx: commands.Context) -> None:
		"""Manage excluded custom commands."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@cclistrefset_exclude.command(name="add")
	async def cclistrefset_exclude_add(self, ctx: commands.Context, *, command_name: str) -> None:
		"""Exclude a custom command from the published list."""
		command_name = command_name.strip()
		if not command_name:
			await ctx.send("Provide a custom command name to exclude.")
			return

		async with self.config.guild(ctx.guild).excluded_commands() as excluded_commands:
			if command_name.casefold() not in {name.casefold() for name in excluded_commands}:
				excluded_commands.append(command_name)

		await self.refresh_guild(ctx.guild)
		await ctx.send(f"Excluded `{command_name}` from the CCListRef list.")

	@cclistrefset_exclude.command(name="remove")
	async def cclistrefset_exclude_remove(self, ctx: commands.Context, *, command_name: str) -> None:
		"""Remove a custom command exclusion."""
		command_name = command_name.strip()
		updated = False
		async with self.config.guild(ctx.guild).excluded_commands() as excluded_commands:
			remaining = [name for name in excluded_commands if name.casefold() != command_name.casefold()]
			updated = len(remaining) != len(excluded_commands)
			excluded_commands[:] = remaining

		if updated:
			await self.refresh_guild(ctx.guild)
			await ctx.send(f"Removed `{command_name}` from the exclusion list.")
			return

		await ctx.send(f"`{command_name}` is not currently excluded.")

	@cclistrefset_exclude.command(name="list")
	async def cclistrefset_exclude_list(self, ctx: commands.Context) -> None:
		"""Show excluded custom commands."""
		excluded_commands = await self.config.guild(ctx.guild).excluded_commands()
		if not excluded_commands:
			await ctx.send("No custom commands are excluded from CCListRef.")
			return

		formatted = ", ".join(sorted(excluded_commands, key=str.casefold))
		await ctx.send(f"Excluded commands: {formatted}")

	@cclistrefset.command(name="refresh")
	async def cclistrefset_refresh(self, ctx: commands.Context) -> None:
		"""Force an immediate refresh of the published list."""
		await self.refresh_guild(ctx.guild)
		await ctx.send("CCListRef refreshed.")


async def setup(bot: commands.Bot):
	await bot.add_cog(CCListRef(bot))
