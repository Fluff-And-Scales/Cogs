from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
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
	_REFRESH_INTERVAL = 600
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
		self.config.register_guild(channel_id=None, message_ids=[], excluded_commands=[], last_snapshot_hash="")
		self._refresh_task: Optional[asyncio.Task] = None
		self._preview_formatter = _PreviewFormatter()
		self._pending_refresh_guild_ids: set[int] = set()
		self._refresh_event = asyncio.Event()

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
			guilds_to_refresh: list[discord.Guild] = []
			try:
				await asyncio.wait_for(self._refresh_event.wait(), timeout=self._REFRESH_INTERVAL)
			except asyncio.TimeoutError:
				guilds_to_refresh = list(self.bot.guilds)
			else:
				self._refresh_event.clear()
				pending_ids = set(self._pending_refresh_guild_ids)
				self._pending_refresh_guild_ids.clear()
				guilds_to_refresh = [guild for guild in self.bot.guilds if guild.id in pending_ids]

			for guild in guilds_to_refresh:
				with contextlib.suppress(Exception):
					await self.refresh_guild(guild)

	def _request_refresh(self, guild: discord.Guild) -> None:
		self._pending_refresh_guild_ids.add(guild.id)
		self._refresh_event.set()

	def _is_customcom_management_command(self, ctx: commands.Context) -> bool:
		command = getattr(ctx, "command", None)
		if command is None:
			return False

		qualified_name = (command.qualified_name or "").casefold().strip()
		root_name = qualified_name.split(maxsplit=1)[0] if qualified_name else ""
		if root_name in {"cc", "customcom", "customcommands"}:
			return True

		cog_name = (getattr(command, "cog_name", "") or "").casefold()
		return cog_name in {name.casefold() for name in self._CUSTOMCOM_COG_NAMES}

	@commands.Cog.listener()
	async def on_command_completion(self, ctx: commands.Context) -> None:
		if ctx.guild is None:
			return
		if not self._is_customcom_management_command(ctx):
			return
		self._request_refresh(ctx.guild)

	async def refresh_guild(self, guild: discord.Guild, *, force: bool = False) -> None:
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
		payloads = self._build_message_payloads(rows)
		snapshot_hash = self._compute_snapshot_hash(payloads)
		config_guild = self.config.guild(guild)
		last_snapshot_hash = await config_guild.last_snapshot_hash()

		if (
			not force
			and snapshot_hash == last_snapshot_hash
			and await self._managed_messages_match(channel, len(payloads))
		):
			return

		published_ids = await self._publish_payloads(channel, payloads)
		if published_ids:
			await config_guild.message_ids.set(published_ids)
			await config_guild.last_snapshot_hash.set(snapshot_hash)

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

		command_table = await self._get_command_table_from_config(config, guild)
		if not command_table:
			return []

		active_names = await self._get_active_custom_command_names(customcom, guild)
		use_active_name_set = bool(active_names)

		row_map: dict[str, dict[str, str]] = {}
		for command_name, command_data in sorted(command_table.items(), key=lambda item: str(item[0]).casefold()):
			if not isinstance(command_name, str):
				continue

			normalized_name = command_name.strip()
			if not normalized_name:
				continue

			normalized_key = normalized_name.casefold()
			if use_active_name_set:
				if normalized_key not in active_names:
					continue
			elif not await self._is_active_custom_command(customcom, guild, normalized_name):
				continue

			output_text, image_url = self._extract_output_preview(command_data)
			row_map[normalized_key] = {
				"name": normalized_name,
				"example": self._build_command_example(normalized_name, command_data),
				"output": output_text,
				"image_url": image_url or "",
			}

		return list(row_map.values())

	async def _get_command_table_from_config(self, config: Any, guild: discord.Guild) -> dict[Any, Any]:
		guild_conf = config.guild(guild)

		for key in self._COMMAND_CONTAINER_KEYS:
			getter = getattr(guild_conf, key, None)
			if getter is None or not callable(getter):
				continue

			with contextlib.suppress(Exception):
				value = getter()
				if hasattr(value, "all") and callable(getattr(value, "all")):
					value = value.all()
				if asyncio.iscoroutine(value):
					value = await value
				if isinstance(value, dict) and self._mapping_looks_like_command_table(value):
					return value

		return {}

	async def _get_active_custom_command_names(self, customcom: Any, guild: discord.Guild) -> set[str]:
		method_names = (
			"get_command_names",
			"get_commands",
			"get_ccs",
			"list_commands",
			"all_commands",
		)

		for method_name in method_names:
			method = getattr(customcom, method_name, None)
			if method is None:
				continue

			for args in ((guild,), (guild.id,), tuple()):
				with contextlib.suppress(Exception):
					result = method(*args)
					if asyncio.iscoroutine(result):
						result = await result
					names = self._extract_command_names_from_result(result)
					if names:
						return names

		return set()

	def _extract_command_names_from_result(self, result: Any) -> set[str]:
		names: set[str] = set()
		if isinstance(result, dict):
			for key in result:
				if isinstance(key, str) and key.strip():
					names.add(key.strip().casefold())
			return names

		if isinstance(result, (list, tuple, set)):
			for item in result:
				if isinstance(item, str) and item.strip():
					names.add(item.strip().casefold())
					continue
				item_name = getattr(item, "name", None)
				if isinstance(item_name, str) and item_name.strip():
					names.add(item_name.strip().casefold())
					continue
				if isinstance(item, dict):
					for key in ("name", "command", "trigger"):
						value = item.get(key)
						if isinstance(value, str) and value.strip():
							names.add(value.strip().casefold())
							break
			return names

		return names

	async def _is_active_custom_command(self, customcom: Any, guild: discord.Guild, command_name: str) -> bool:
		lookup_methods = ("get_command", "get_cc", "get_custom_command", "get_customcom")
		attempted_lookup = False

		for method_name in lookup_methods:
			method = getattr(customcom, method_name, None)
			if method is None:
				continue

			attempted_lookup = True
			result: Any = None
			with contextlib.suppress(Exception):
				result = method(guild, command_name)

			if result is None:
				with contextlib.suppress(Exception):
					result = method(command_name)

			if asyncio.iscoroutine(result):
				with contextlib.suppress(Exception):
					result = await result

			if result:
				return True

		if not attempted_lookup:
			return False

		return False

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

	def _build_message_payloads(self, rows: list[dict[str, str]]) -> list[tuple[str, list[discord.Embed]]]:
		header_content = f"{self._TITLE}\n{self._NOTE}"

		if not rows:
			empty_embed = discord.Embed(title="No Custom Commands", description="No custom commands available.")
			return [(header_content, [empty_embed])]

		command_embeds: list[discord.Embed] = []
		for row in rows:
			embed = discord.Embed(title=row["name"], description=row["output"])
			embed.add_field(name="Example", value=row["example"], inline=False)
			if row["image_url"]:
				embed.set_image(url=row["image_url"])
			command_embeds.append(embed)

		payloads: list[tuple[str, list[discord.Embed]]] = []
		for index in range(0, len(command_embeds), 10):
			chunk = command_embeds[index:index + 10]
			content = header_content if index == 0 else f"{self._TITLE} (continued)"
			payloads.append((content, chunk))

		return payloads

	def _compute_snapshot_hash(self, payloads: list[tuple[str, list[discord.Embed]]]) -> str:
		normalized_payloads = [
			{
				"content": content,
				"embeds": [embed.to_dict() for embed in embeds],
			}
			for content, embeds in payloads
		]
		serialized = json.dumps(normalized_payloads, sort_keys=True, separators=(",", ":"))
		return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

	async def _managed_messages_match(self, channel: discord.TextChannel, expected_count: int) -> bool:
		stored_ids = await self.config.guild(channel.guild).message_ids()
		if len(stored_ids) != expected_count:
			return False
		for message_id in stored_ids:
			try:
				await channel.fetch_message(message_id)
			except (discord.NotFound, discord.Forbidden, discord.HTTPException):
				return False
		return True

	async def _publish_payloads(self, channel: discord.TextChannel, payloads: list[tuple[str, list[discord.Embed]]]) -> list[int]:
		me = channel.guild.me
		if me is None:
			return []

		permissions = channel.permissions_for(me)
		if not permissions.send_messages:
			return []

		stored_ids = await self.config.guild(channel.guild).message_ids()
		existing_messages: list[discord.Message] = []
		for message_id in stored_ids:
			with contextlib.suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
				existing_messages.append(await channel.fetch_message(message_id))

		published_ids: list[int] = []
		for index, (content, embeds) in enumerate(payloads):
			message = existing_messages[index] if index < len(existing_messages) else None
			if message is None:
				with contextlib.suppress(discord.Forbidden, discord.HTTPException):
					message = await channel.send(content=content, embeds=embeds)
				if message is None:
					continue
			else:
				current_embeds = [embed.to_dict() for embed in message.embeds]
				target_embeds = [embed.to_dict() for embed in embeds]
				if message.content != content or current_embeds != target_embeds:
					try:
						await message.edit(content=content, embeds=embeds)
					except (discord.Forbidden, discord.NotFound, discord.HTTPException):
						replacement_message = None
						with contextlib.suppress(discord.Forbidden, discord.HTTPException):
							replacement_message = await channel.send(content=content, embeds=embeds)
						if replacement_message is not None:
							with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
								await message.delete()
							message = replacement_message

			published_ids.append(message.id)

		for message in existing_messages[len(payloads):]:
			with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
				await message.delete()

		return published_ids

	async def _delete_legacy_messages(self, channel: discord.TextChannel, keep_message_ids: Optional[set[int]] = None) -> None:
		if self.bot.user is None:
			return

		keep_message_ids = keep_message_ids or set()
		with contextlib.suppress(discord.Forbidden, discord.HTTPException):
			async for message in channel.history(limit=200):
				if message.author.id != self.bot.user.id:
					continue
				if message.id in keep_message_ids:
					continue
				if self._is_cclistref_message(message):
					with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
						await message.delete()

	def _is_cclistref_message(self, message: discord.Message) -> bool:
		content = (message.content or "").strip()
		if content.startswith(self._TITLE):
			return True
		if self._NOTE in content:
			return True

		for embed in message.embeds:
			title = (embed.title or "").strip()
			description = (embed.description or "").strip()
			if title in {self._TITLE, "No Custom Commands", "Additional Commands Not Shown"}:
				return True
			if description == self._NOTE:
				return True
			if any((field.name or "").strip() == "Example" for field in embed.fields):
				return True

		return False

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
		await self.config.guild(ctx.guild).last_snapshot_hash.set("")

		if old_channel is not None and old_channel.id != channel.id:
			await self._delete_managed_messages(old_channel, old_message_ids)

		await self._delete_legacy_messages(channel)
		await self.refresh_guild(ctx.guild, force=True)
		await ctx.send(f"CCListRef will post in {channel.mention}.")

	@cclistrefset.command(name="clear")
	async def cclistrefset_clear(self, ctx: commands.Context) -> None:
		"""Clear the configured output channel."""
		old_channel = await self.get_output_channel(ctx.guild)
		old_message_ids = await self.config.guild(ctx.guild).message_ids()
		if old_channel is not None:
			await self._delete_managed_messages(old_channel, old_message_ids)
			await self._delete_legacy_messages(old_channel)

		await self.config.guild(ctx.guild).channel_id.clear()
		await self.config.guild(ctx.guild).message_ids.set([])
		await self.config.guild(ctx.guild).last_snapshot_hash.set("")
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

		self._request_refresh(ctx.guild)
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
			self._request_refresh(ctx.guild)
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
		await self.refresh_guild(ctx.guild, force=True)
		await ctx.send("CCListRef refreshed.")

	@cclistrefset.command(name="cleanup")
	async def cclistrefset_cleanup(self, ctx: commands.Context) -> None:
		"""Remove untracked legacy CCListRef messages from the configured channel."""
		channel = await self.get_output_channel(ctx.guild)
		if channel is None:
			await ctx.send("CCListRef does not have an output channel configured.")
			return

		stored_ids = await self.config.guild(ctx.guild).message_ids()
		await self._delete_legacy_messages(channel, keep_message_ids=set(stored_ids))
		await ctx.send("CCListRef legacy message cleanup complete.")


async def setup(bot: commands.Bot):
	await bot.add_cog(CCListRef(bot))
