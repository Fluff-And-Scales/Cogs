from __future__ import annotations

import contextlib
import copy
import re
import time
from collections import defaultdict
from typing import Any, Optional

import discord
from redbot.core import Config, commands


class CCWordReact(commands.Cog):
	"""Run custom commands when a specific user says a specific word."""

	_CUSTOMCOM_COG_NAMES = ("CustomCom", "CustomCommands", "customcom")
	_TRIGGER_WINDOW_SECONDS = 10
	_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\s*\((https?://[^)\s]+)\)", re.IGNORECASE)
	_URL_RE = re.compile(r"https?://[^\s)>]+", re.IGNORECASE)
	_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg")

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.config = Config.get_conf(self, identifier=853217640112, force_registration=True)
		self.config.register_guild(
			triggers=[],
			output_channels={"text": None, "image": None},
			global_cooldown_seconds=0.0,
		)
		self._pending_trigger_responses: dict[int, list[float]] = defaultdict(list)

	def _get_customcom_cog(self) -> Optional[Any]:
		for name in self._CUSTOMCOM_COG_NAMES:
			cog = self.bot.get_cog(name)
			if cog is not None:
				return cog
		return None

	def _candidate_command_names(self, command_name: str) -> list[str]:
		raw = command_name.strip()
		token = raw.split(maxsplit=1)[0] if raw else ""
		candidates = [raw, token]
		cleaned: list[str] = []
		seen: set[str] = set()
		for candidate in candidates:
			candidate = candidate.strip()
			if candidate.startswith("!"):
				candidate = candidate[1:]
			if not candidate:
				continue
			key = candidate.casefold()
			if key in seen:
				continue
			seen.add(key)
			cleaned.append(candidate)
		return cleaned

	async def _custom_command_exists_in_config(self, customcom: Any, guild: discord.Guild, names: list[str]) -> bool:
		config = getattr(customcom, "config", None)
		if config is None:
			return False

		normalized = {name.casefold() for name in names}
		guild_conf = config.guild(guild)
		for container_name in ("commands", "ccs", "custom_commands", "customcom", "cmds"):
			getter = getattr(guild_conf, container_name, None)
			if getter is None or not callable(getter):
				continue
			with contextlib.suppress(Exception):
				value = getter()
				if hasattr(value, "all") and callable(getattr(value, "all")):
					value = value.all()
				if hasattr(value, "__await__"):
					value = await value
				if isinstance(value, dict):
					keys = {str(key).strip().casefold() for key in value if isinstance(key, str)}
					if normalized & keys:
						return True

		return False

	async def _custom_command_exists(self, guild: discord.Guild, command_name: str) -> bool:
		customcom = self._get_customcom_cog()
		if customcom is None:
			return False

		candidate_names = self._candidate_command_names(command_name)
		if not candidate_names:
			return False

		for method_name in ("get_command", "get_cc", "get_custom_command", "get_customcom"):
			method = getattr(customcom, method_name, None)
			if method is None:
				continue

			for name in candidate_names:
				result: Any = None
				for args in ((guild, name), (name, guild), (name,)):
					try:
						result = method(*args)
					except Exception:
						result = None
					if result is not None:
						break

				if result is not None and hasattr(result, "__await__"):
					try:
						result = await result
					except Exception:
						result = None

				if result:
					return True

		return await self._custom_command_exists_in_config(customcom, guild, candidate_names)

	def _matches_word(self, content: str, word: str) -> bool:
		pattern = rf"(?<!\w){re.escape(word)}(?!\w)"
		return re.search(pattern, content, flags=re.IGNORECASE) is not None

	def _resolve_member(self, guild: discord.Guild, user_ref: str) -> Optional[discord.Member]:
		candidate = user_ref.strip()
		if not candidate:
			return None

		mention_match = re.fullmatch(r"<@!?(\d+)>", candidate)
		if mention_match:
			member = guild.get_member(int(mention_match.group(1)))
			if member is not None:
				return member

		if candidate.isdigit():
			member = guild.get_member(int(candidate))
			if member is not None:
				return member

		member = guild.get_member_named(candidate)
		if member is not None:
			return member

		normalized = candidate.casefold()
		for guild_member in guild.members:
			display_name = (guild_member.display_name or "").casefold()
			username = (guild_member.name or "").casefold()
			global_name = (getattr(guild_member, "global_name", None) or "").casefold()
			if normalized in {display_name, username, global_name}:
				return guild_member

		return None

	def _trigger_is_global(self, trigger: dict[str, Any]) -> bool:
		trigger_user_id = trigger.get("user_id")
		if trigger_user_id is None:
			return True
		if isinstance(trigger_user_id, str):
			return trigger_user_id.strip().casefold() in {"*", "all", "global"}
		return False

	def _trigger_matches_exact_user(self, trigger: dict[str, Any], user_id: int) -> bool:
		if self._trigger_is_global(trigger):
			return False
		trigger_user_id = trigger.get("user_id")
		if isinstance(trigger_user_id, int):
			return trigger_user_id == user_id
		if isinstance(trigger_user_id, str):
			with contextlib.suppress(ValueError):
				return int(trigger_user_id.strip()) == user_id
		return False

	def _trigger_matches_user(self, trigger: dict[str, Any], user_id: int) -> bool:
		return self._trigger_is_global(trigger) or self._trigger_matches_exact_user(trigger, user_id)

	def _trigger_matches_rule(self, trigger: dict[str, Any], user_id: int, word: str) -> bool:
		return self._trigger_matches_user(trigger, user_id) and str(trigger.get("word", "")).casefold() == word.casefold()

	def _same_trigger_target(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
		if self._trigger_is_global(left) and self._trigger_is_global(right):
			return True
		if self._trigger_is_global(left) or self._trigger_is_global(right):
			return False
		left_user = left.get("user_id")
		right_user = right.get("user_id")
		return str(left_user).strip() == str(right_user).strip()

	async def _get_output_channel_for_type(
		self,
		guild: discord.Guild,
		output_type: str,
		fallback_channel: discord.abc.Messageable,
	) -> discord.abc.Messageable:
		with contextlib.suppress(Exception):
			output_channels = await self.config.guild(guild).output_channels()
			channel_id = output_channels.get(output_type)
			if isinstance(channel_id, int):
				channel = guild.get_channel(channel_id)
				if isinstance(channel, discord.TextChannel):
					return channel

		return fallback_channel

	def _mark_triggered_channel(self, channel_id: int) -> None:
		now = time.monotonic()
		expiry = now + self._TRIGGER_WINDOW_SECONDS
		entries = self._pending_trigger_responses[channel_id]
		entries.append(expiry)

	def _consume_triggered_channel(self, channel_id: int) -> bool:
		now = time.monotonic()
		entries = self._pending_trigger_responses.get(channel_id, [])
		entries = [expiry for expiry in entries if expiry >= now]
		if not entries:
			self._pending_trigger_responses.pop(channel_id, None)
			return False

		entries.pop(0)
		if entries:
			self._pending_trigger_responses[channel_id] = entries
		else:
			self._pending_trigger_responses.pop(channel_id, None)
		return True

	def _is_image_url(self, value: str) -> bool:
		target = value.strip().strip("<>")
		target_lower = target.casefold()
		if not (target_lower.startswith("http://") or target_lower.startswith("https://")):
			return False
		path = target_lower.split("?", 1)[0]
		return path.endswith(self._IMAGE_EXTENSIONS)

	def _sanitize_markdown_image_text(self, content: str) -> tuple[Optional[str], bool]:
		original = content
		cleaned = self._MARKDOWN_IMAGE_RE.sub("", content)

		def _replace_url(match: re.Match[str]) -> str:
			url = match.group(0)
			return "" if self._is_image_url(url) else url

		cleaned = self._URL_RE.sub(_replace_url, cleaned)
		cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
		cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
		if cleaned == original:
			return original, False
		return (cleaned if cleaned else None), True

	def _extract_image_urls_from_message(self, message: discord.Message) -> list[str]:
		urls: list[str] = []

		for match in self._MARKDOWN_IMAGE_RE.finditer(message.content or ""):
			url = match.group(1).strip()
			if self._is_image_url(url):
				urls.append(url.strip("<>"))

		for match in self._URL_RE.finditer(message.content or ""):
			url = match.group(0).strip()
			if self._is_image_url(url):
				urls.append(url.strip("<>"))

		for embed in message.embeds:
			if embed.image and embed.image.url and self._is_image_url(embed.image.url):
				urls.append(embed.image.url.strip("<>"))
			if embed.thumbnail and embed.thumbnail.url and self._is_image_url(embed.thumbnail.url):
				urls.append(embed.thumbnail.url.strip("<>"))

		for attachment in message.attachments:
			attachment_url = str(getattr(attachment, "url", "") or "").strip()
			if attachment_url and self._is_image_url(attachment_url):
				urls.append(attachment_url.strip("<>"))

		unique_urls: list[str] = []
		seen: set[str] = set()
		for url in urls:
			key = url.casefold()
			if key in seen:
				continue
			seen.add(key)
			unique_urls.append(url)

		return unique_urls

	async def _maybe_sanitize_trigger_response(self, message: discord.Message) -> None:
		if not self._consume_triggered_channel(message.channel.id):
			return

		image_urls = self._extract_image_urls_from_message(message)
		if image_urls:
			target_channel = await self._get_output_channel_for_type(message.guild, "image", message.channel)
			payload = "\n".join(image_urls)
			with contextlib.suppress(discord.Forbidden, discord.HTTPException):
				await target_channel.send(payload)
			with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
				await message.delete()
			return

		new_content, changed = self._sanitize_markdown_image_text(message.content or "")
		final_content = new_content if changed else (message.content or None)
		target_channel = await self._get_output_channel_for_type(message.guild, "text", message.channel)
		if target_channel.id != message.channel.id:
			if final_content:
				with contextlib.suppress(discord.Forbidden, discord.HTTPException):
					await target_channel.send(final_content)
			with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
				await message.delete()
			return

		if not changed:
			return

		try:
			await message.edit(content=new_content)
		except (discord.Forbidden, discord.NotFound, discord.HTTPException):
			if new_content is None and not message.embeds and not message.attachments:
				with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
					await message.delete()

	async def _run_custom_command(self, message: discord.Message, command_name: str) -> None:
		self._mark_triggered_channel(message.channel.id)
		prefixes = self.bot.get_prefix(message)
		if hasattr(prefixes, "__await__"):
			prefixes = await prefixes

		if isinstance(prefixes, str):
			prefix = prefixes
		else:
			prefix = next((p for p in prefixes if isinstance(p, str)), "!")

		fake_message = copy.copy(message)
		fake_message.content = f"{prefix}{command_name}"
		fake_message.id = 0
		await self.bot.process_commands(fake_message)

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message) -> None:
		if message.guild is None:
			return

		if message.author.bot:
			if self.bot.user is not None and message.author.id == self.bot.user.id:
				await self._maybe_sanitize_trigger_response(message)
			return

		guild_conf = self.config.guild(message.guild)
		triggers = await guild_conf.triggers()
		if not triggers:
			return
		global_cooldown_seconds = max(0.0, float(await guild_conf.global_cooldown_seconds() or 0))

		now = time.time()
		for trigger in triggers:
			if not self._trigger_matches_user(trigger, message.author.id):
				continue

			word = str(trigger.get("word", "")).strip()
			command_name = str(trigger.get("command_name", "")).strip()
			if not word or not command_name:
				continue

			if not self._matches_word(message.content, word):
				continue

			rule_cooldown_seconds = float(trigger.get("cooldown_seconds", 0) or 0)
			effective_cooldown_seconds = rule_cooldown_seconds if rule_cooldown_seconds > 0 else global_cooldown_seconds
			last_triggered_at = float(trigger.get("last_triggered_at", 0) or 0)
			if effective_cooldown_seconds > 0 and now - last_triggered_at < effective_cooldown_seconds:
				continue

			async with self.config.guild(message.guild).triggers() as live_triggers:
				for live_trigger in live_triggers:
					if (
						self._same_trigger_target(live_trigger, trigger)
						and str(live_trigger.get("word", "")).casefold() == word.casefold()
						and str(live_trigger.get("command_name", "")).strip().casefold() == command_name.casefold()
					):
						live_trigger["last_triggered_at"] = now
						break

			await self._run_custom_command(message, command_name)
			return

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	@commands.admin_or_permissions(manage_guild=True)
	async def ccwordreactset(self, ctx: commands.Context) -> None:
		"""Configure user word -> custom command triggers."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@ccwordreactset.command(name="add")
	async def ccwordreactset_add(
		self,
		ctx: commands.Context,
		user_ref: str,
		word: str,
		command_name: str,
		cooldown_seconds: float = 0,
	) -> None:
		"""Add a trigger: when <user> says <word>, run <command_name>. Use user_ref `*` for global."""
		is_global = user_ref.strip().casefold() in {"*", "all", "global"}
		user: Optional[discord.Member] = None
		if not is_global:
			user = self._resolve_member(ctx.guild, user_ref)
			if user is None:
				await ctx.send(f"Could not find a member matching `{user_ref}`.")
				return

		word = word.strip()
		command_name = command_name.strip()
		cooldown_seconds = max(0.0, float(cooldown_seconds))
		if not word or not command_name:
			await ctx.send("Word and command name are required.")
			return

		if not await self._custom_command_exists(ctx.guild, command_name):
			await ctx.send(f"Custom command `{command_name}` was not found.")
			return

		new_trigger = {
			"user_id": "*" if is_global else user.id,
			"word": word,
			"command_name": command_name,
			"cooldown_seconds": cooldown_seconds,
			"last_triggered_at": 0,
		}

		async with self.config.guild(ctx.guild).triggers() as triggers:
			for existing in triggers:
				if is_global:
					matches_target = self._trigger_is_global(existing)
				else:
					matches_target = user is not None and self._trigger_matches_exact_user(existing, user.id)
				if matches_target and str(existing.get("word", "")).casefold() == word.casefold():
					existing["user_id"] = "*" if is_global else user.id
					existing["command_name"] = command_name
					existing["cooldown_seconds"] = cooldown_seconds
					existing.setdefault("last_triggered_at", 0)
					break
			else:
				triggers.append(new_trigger)

		if is_global:
			await ctx.send(
				f"Global trigger set: when anyone says `{word}`, run custom command `{command_name}` (cooldown: {cooldown_seconds:g}s)."
			)
		else:
			await ctx.send(
				f"Trigger set: when {user.mention} says `{word}`, run custom command `{command_name}` (cooldown: {cooldown_seconds:g}s)."
			)

	@ccwordreactset.command(name="remove")
	async def ccwordreactset_remove(self, ctx: commands.Context, user_ref: str, word: str) -> None:
		"""Remove a trigger by user and word. Use user_ref `*` for global triggers."""
		is_global = user_ref.strip().casefold() in {"*", "all", "global"}
		user: Optional[discord.Member] = None
		if not is_global:
			user = self._resolve_member(ctx.guild, user_ref)
			if user is None:
				await ctx.send(f"Could not find a member matching `{user_ref}`.")
				return

		word = word.strip()
		removed = False
		async with self.config.guild(ctx.guild).triggers() as triggers:
			remaining = []
			for trigger in triggers:
				if is_global:
					matches_target = self._trigger_is_global(trigger)
				else:
					matches_target = user is not None and self._trigger_matches_exact_user(trigger, user.id)
				if matches_target and str(trigger.get("word", "")).casefold() == word.casefold():
					removed = True
					continue
				remaining.append(trigger)
			triggers[:] = remaining

		if removed:
			if is_global:
				await ctx.send(f"Removed global trigger for `{word}`.")
			else:
				await ctx.send(f"Removed trigger for {user.mention} saying `{word}`.")
		else:
			await ctx.send("No matching trigger found.")

	@ccwordreactset.command(name="cooldown")
	async def ccwordreactset_cooldown(self, ctx: commands.Context, user_ref: str, word: str, seconds: float) -> None:
		"""Set per-rule cooldown seconds. Use user_ref `*` for global triggers. Set 0 to use global cooldown."""
		is_global = user_ref.strip().casefold() in {"*", "all", "global"}
		user: Optional[discord.Member] = None
		if not is_global:
			user = self._resolve_member(ctx.guild, user_ref)
			if user is None:
				await ctx.send(f"Could not find a member matching `{user_ref}`.")
				return

		seconds = max(0.0, float(seconds))
		updated = False
		async with self.config.guild(ctx.guild).triggers() as triggers:
			for trigger in triggers:
				if is_global:
					matches_target = self._trigger_is_global(trigger)
				else:
					matches_target = user is not None and self._trigger_matches_exact_user(trigger, user.id)
				if matches_target and str(trigger.get("word", "")).casefold() == word.casefold():
					trigger["cooldown_seconds"] = seconds
					trigger.setdefault("last_triggered_at", 0)
					updated = True
					break

		if not updated:
			await ctx.send("No matching trigger found.")
			return

		if is_global:
			if seconds > 0:
				await ctx.send(f"Cooldown set to {seconds:g}s for global trigger `{word}`.")
			else:
				await ctx.send(f"Per-rule cooldown cleared for global trigger `{word}`; it now uses the global cooldown setting.")
		else:
			if seconds > 0:
				await ctx.send(f"Cooldown set to {seconds:g}s for {user.mention} saying `{word}`.")
			else:
				await ctx.send(f"Per-rule cooldown cleared for {user.mention} saying `{word}`; it now uses the global cooldown setting.")

	@ccwordreactset.command(name="globalcooldown")
	async def ccwordreactset_globalcooldown(self, ctx: commands.Context, seconds: float) -> None:
		"""Set global cooldown seconds used when a trigger's per-rule cooldown is 0."""
		seconds = max(0.0, float(seconds))
		await self.config.guild(ctx.guild).global_cooldown_seconds.set(seconds)
		await ctx.send(f"Global cooldown set to {seconds:g}s.")

	@ccwordreactset.group(name="output", invoke_without_command=True)
	async def ccwordreactset_output(self, ctx: commands.Context) -> None:
		"""Configure output channels by output type (text/image)."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@ccwordreactset_output.command(name="set")
	async def ccwordreactset_output_set(
		self,
		ctx: commands.Context,
		output_type: str,
		channel: discord.TextChannel,
	) -> None:
		"""Set output channel for text or image output."""
		normalized_type = output_type.casefold().strip()
		if normalized_type not in {"text", "image"}:
			await ctx.send("Output type must be `text` or `image`.")
			return

		async with self.config.guild(ctx.guild).output_channels() as output_channels:
			output_channels[normalized_type] = channel.id

		await ctx.send(f"{normalized_type.title()} output will be posted in {channel.mention}.")

	@ccwordreactset_output.command(name="clear")
	async def ccwordreactset_output_clear(self, ctx: commands.Context, output_type: str) -> None:
		"""Clear output channel override for text or image output."""
		normalized_type = output_type.casefold().strip()
		if normalized_type not in {"text", "image"}:
			await ctx.send("Output type must be `text` or `image`.")
			return

		async with self.config.guild(ctx.guild).output_channels() as output_channels:
			output_channels[normalized_type] = None

		await ctx.send(
			f"{normalized_type.title()} output channel reset to trigger channel default."
		)

	@ccwordreactset_output.command(name="show")
	async def ccwordreactset_output_show(self, ctx: commands.Context) -> None:
		"""Show output channel routing for text and image output."""
		output_channels = await self.config.guild(ctx.guild).output_channels()
		lines: list[str] = []
		for output_type in ("text", "image"):
			channel_id = output_channels.get(output_type)
			channel = ctx.guild.get_channel(channel_id) if isinstance(channel_id, int) else None
			if isinstance(channel, discord.TextChannel):
				lines.append(f"- {output_type}: {channel.mention}")
			else:
				lines.append(f"- {output_type}: trigger channel (default)")

		await ctx.send("Output routing:\n" + "\n".join(lines))

	@ccwordreactset.command(name="list")
	async def ccwordreactset_list(self, ctx: commands.Context) -> None:
		"""List configured triggers."""
		guild_conf = self.config.guild(ctx.guild)
		triggers = await guild_conf.triggers()
		global_cooldown_seconds = max(0.0, float(await guild_conf.global_cooldown_seconds() or 0))
		if not triggers:
			await ctx.send(f"No CCWordReact triggers configured. Global cooldown: {global_cooldown_seconds:g}s")
			return

		lines: list[str] = [f"Global cooldown fallback: {global_cooldown_seconds:g}s"]
		for trigger in triggers:
			user_id = trigger.get("user_id")
			word = str(trigger.get("word", "")).strip()
			command_name = str(trigger.get("command_name", "")).strip()
			rule_cooldown_seconds = float(trigger.get("cooldown_seconds", 0) or 0)
			effective_cooldown_seconds = rule_cooldown_seconds if rule_cooldown_seconds > 0 else global_cooldown_seconds
			if self._trigger_is_global(trigger):
				user_display = "Global (any user)"
			else:
				member: Optional[discord.Member] = None
				if isinstance(user_id, int):
					member = ctx.guild.get_member(user_id)
				elif isinstance(user_id, str):
					with contextlib.suppress(ValueError):
						member = ctx.guild.get_member(int(user_id.strip()))
				user_display = member.mention if member is not None else f"UserID {user_id}"
			cooldown_source = "rule" if rule_cooldown_seconds > 0 else "global"
			lines.append(
				f"- {user_display} -> `{word}` => `{command_name}` (cooldown: {effective_cooldown_seconds:g}s, source: {cooldown_source})"
			)

		await ctx.send("Configured triggers:\n" + "\n".join(lines))

	@ccwordreactset.command(name="clear")
	async def ccwordreactset_clear(self, ctx: commands.Context) -> None:
		"""Clear all triggers for this server."""
		await self.config.guild(ctx.guild).triggers.set([])
		await ctx.send("Cleared all CCWordReact triggers for this server.")


async def setup(bot: commands.Bot):
	await bot.add_cog(CCWordReact(bot))
