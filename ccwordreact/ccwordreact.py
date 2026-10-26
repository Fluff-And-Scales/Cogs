from __future__ import annotations

import copy
import contextlib
import re
from typing import Any, Optional

import discord
from redbot.core import Config, commands


class CCWordReact(commands.Cog):
	"""Run custom commands when a specific user says a specific word."""

	_CUSTOMCOM_COG_NAMES = ("CustomCom", "CustomCommands", "customcom")

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.config = Config.get_conf(self, identifier=853217640112, force_registration=True)
		self.config.register_guild(triggers=[])

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

	async def _run_custom_command(self, message: discord.Message, command_name: str) -> None:
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
		if message.guild is None or message.author.bot:
			return

		triggers = await self.config.guild(message.guild).triggers()
		if not triggers:
			return

		for trigger in triggers:
			if trigger.get("user_id") != message.author.id:
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
		user: discord.Member,
		word: str,
		command_name: str,
	) -> None:
		"""Add a trigger: when <user> says <word>, run <command_name>."""
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
			"word": word,
			"command_name": command_name,
		}

		async with self.config.guild(ctx.guild).triggers() as triggers:
			for existing in triggers:
				if (
					existing.get("user_id") == user.id
					and str(existing.get("word", "")).casefold() == word.casefold()
				):
					existing["command_name"] = command_name
					break
			else:
				triggers.append(new_trigger)

		await ctx.send(
			f"Trigger set: when {user.mention} says `{word}`, run custom command `{command_name}`."
		)

	@ccwordreactset.command(name="remove")
	async def ccwordreactset_remove(self, ctx: commands.Context, user: discord.Member, word: str) -> None:
		"""Remove a trigger by user and word."""
		word = word.strip()
		removed = False
		async with self.config.guild(ctx.guild).triggers() as triggers:
			remaining = []
			for trigger in triggers:
				if (
					trigger.get("user_id") == user.id
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
			member = ctx.guild.get_member(user_id) if isinstance(user_id, int) else None
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
