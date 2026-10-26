from __future__ import annotations

import copy
import contextlib
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
		self.config.register_guild(triggers=[])
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

	def _trigger_matches_user(self, trigger: dict[str, Any], user_id: int) -> bool:
		trigger_user_id = trigger.get("user_id")
		if isinstance(trigger_user_id, int):
			return trigger_user_id == user_id
		if isinstance(trigger_user_id, str):
			with contextlib.suppress(ValueError):
				return int(trigger_user_id.strip()) == user_id
		return False

	def _trigger_matches_member(self, trigger: dict[str, Any], member: discord.Member) -> bool:
		if self._trigger_matches_user(trigger, member.id):
			return True

		stored_names = {
			str(trigger.get("user_name", "")).casefold().strip(),
			str(trigger.get("user_display_name", "")).casefold().strip(),
			str(trigger.get("user_global_name", "")).casefold().strip(),
		}
		stored_names.discard("")
		if not stored_names:
			return False

		current_names = {
			(member.name or "").casefold().strip(),
			(member.display_name or "").casefold().strip(),
			(getattr(member, "global_name", None) or "").casefold().strip(),
		}
		current_names.discard("")
		return bool(stored_names & current_names)

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
			payload = "\n".join(image_urls)
			with contextlib.suppress(discord.Forbidden, discord.HTTPException):
				await message.channel.send(payload)
			with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
				await message.delete()
			return

		new_content, changed = self._sanitize_markdown_image_text(message.content or "")
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
		await self.bot.process_commands(fake_message)

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message) -> None:
		if message.guild is None:
			return

		if message.author.bot:
			if self.bot.user is not None and message.author.id == self.bot.user.id:
				await self._maybe_sanitize_trigger_response(message)
			return

		triggers = await self.config.guild(message.guild).triggers()
		if not triggers:
			return

		for trigger in triggers:
			if not self._trigger_matches_member(trigger, message.author):
				continue

			word = trigger.get("word", "").strip()
			command_name = trigger.get("command_name", "").strip()
			if not word or not command_name:
				continue

			if not self._matches_word(message.content, word):
				continue

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
	) -> None:
		"""Add a trigger: when <user> says <word>, run <command_name>."""
		user = self._resolve_member(ctx.guild, user_ref)
		if user is None:
			await ctx.send(f"Could not find a member matching `{user_ref}`.")
			return

		word = word.strip()
		command_name = command_name.strip()
		if not word or not command_name:
			await ctx.send("Word and command name are required.")
			return

		if not await self._custom_command_exists(ctx.guild, command_name):
			await ctx.send(f"Custom command `{command_name}` was not found.")
			return

		new_trigger = {
			"user_id": user.id,
			"user_name": user.name,
			"user_display_name": user.display_name,
			"user_global_name": getattr(user, "global_name", None),
			"word": word,
			"command_name": command_name,
		}

		async with self.config.guild(ctx.guild).triggers() as triggers:
			for existing in triggers:
				if (
					self._trigger_matches_user(existing, user.id)
					and str(existing.get("word", "")).casefold() == word.casefold()
				):
					existing["user_id"] = user.id
					existing["user_name"] = user.name
					existing["user_display_name"] = user.display_name
					existing["user_global_name"] = getattr(user, "global_name", None)
					existing["command_name"] = command_name
					break
			else:
				triggers.append(new_trigger)

		await ctx.send(
			f"Trigger set: when {user.mention} says `{word}`, run custom command `{command_name}`."
		)

	@ccwordreactset.command(name="remove")
	async def ccwordreactset_remove(self, ctx: commands.Context, user_ref: str, word: str) -> None:
		"""Remove a trigger by user and word."""
		user = self._resolve_member(ctx.guild, user_ref)
		if user is None:
			await ctx.send(f"Could not find a member matching `{user_ref}`.")
			return

		word = word.strip()
		removed = False
		async with self.config.guild(ctx.guild).triggers() as triggers:
			remaining = []
			for trigger in triggers:
				if (
					self._trigger_matches_user(trigger, user.id)
					and str(trigger.get("word", "")).casefold() == word.casefold()
				):
					removed = True
					continue
				remaining.append(trigger)
			triggers[:] = remaining

		if removed:
			await ctx.send(f"Removed trigger for {user.mention} saying `{word}`.")
		else:
			await ctx.send("No matching trigger found.")

	@ccwordreactset.command(name="list")
	async def ccwordreactset_list(self, ctx: commands.Context) -> None:
		"""List configured triggers."""
		triggers = await self.config.guild(ctx.guild).triggers()
		if not triggers:
			await ctx.send("No CCWordReact triggers configured.")
			return

		lines: list[str] = []
		for trigger in triggers:
			user_id = trigger.get("user_id")
			word = str(trigger.get("word", "")).strip()
			command_name = str(trigger.get("command_name", "")).strip()
			member: Optional[discord.Member] = None
			if isinstance(user_id, int):
				member = ctx.guild.get_member(user_id)
			elif isinstance(user_id, str):
				with contextlib.suppress(ValueError):
					member = ctx.guild.get_member(int(user_id.strip()))
			user_display = member.mention if member is not None else f"UserID {user_id}"
			lines.append(f"- {user_display} -> `{word}` => `{command_name}`")

		await ctx.send("Configured triggers:\n" + "\n".join(lines))

	@ccwordreactset.command(name="clear")
	async def ccwordreactset_clear(self, ctx: commands.Context) -> None:
		"""Clear all triggers for this server."""
		await self.config.guild(ctx.guild).triggers.set([])
		await ctx.send("Cleared all CCWordReact triggers for this server.")


async def setup(bot: commands.Bot):
	await bot.add_cog(CCWordReact(bot))
