from __future__ import annotations

import asyncio
import contextlib
import re
import textwrap
from typing import Any, Optional

import discord
from redbot.core import Config, commands


class CCListRef(commands.Cog):
	"""Maintain a posted reference list for CustomCom commands."""

	_CUSTOMCOM_COG_NAMES = ("CustomCom", "CustomCommands", "customcom")
	_REFRESH_INTERVAL = 60
	_NAME_WIDTH = 20
	_EXAMPLE_WIDTH = 24
	_OUTPUT_WIDTH = 60
	_TITLE = "Command List"
	_NOTE = "An '!' has to be in front of all commands for them to work."

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.config = Config.get_conf(self, identifier=948275610234, force_registration=True)
		self.config.register_guild(channel_id=None, message_ids=[], excluded_commands=[])
		self._refresh_task: Optional[asyncio.Task] = None

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
		pages = self._render_pages(rows)
		await self._publish_pages(channel, pages)

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

		rows: list[dict[str, str]] = []
		for command_name, command_data in sorted(command_table.items(), key=lambda item: str(item[0]).casefold()):
			if not isinstance(command_name, str):
				continue

			rows.append(
				{
					"name": command_name.strip(),
					"example": self._build_command_example(command_name),
					"output": self._extract_output_preview(command_data),
				}
			)

		return rows

	def _find_command_table(self, value: Any) -> Optional[dict[Any, Any]]:
		if isinstance(value, dict):
			if self._mapping_looks_like_command_table(value):
				return value

			for nested in value.values():
				result = self._find_command_table(nested)
				if result is not None:
					return result

		elif isinstance(value, list):
			for item in value:
				result = self._find_command_table(item)
				if result is not None:
					return result

		return None

	def _mapping_looks_like_command_table(self, mapping: dict[Any, Any]) -> bool:
		response_keys = {
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
		if not mapping:
			return False

		for key, item in mapping.items():
			if not isinstance(key, str):
				return False
			if isinstance(item, dict) and any(field in item for field in response_keys):
				return True
			if isinstance(item, (str, list, tuple)):
				return True
		return False

	def _build_command_example(self, command_name: str) -> str:
		return f"!{command_name.strip()}"

	def _extract_output_preview(self, value: Any) -> str:
		preview = self._flatten_preview_value(value)
		preview = re.sub(r"\s+", " ", preview).strip()
		return preview or "(no text output)"

	def _flatten_preview_value(self, value: Any) -> str:
		if isinstance(value, str):
			return value

		if isinstance(value, dict):
			for key in ("content", "response", "text", "message", "reply", "output"):
				candidate = value.get(key)
				if isinstance(candidate, str) and candidate.strip():
					return candidate
				if isinstance(candidate, (list, tuple)):
					joined = " | ".join(self._flatten_preview_value(item) for item in candidate)
					if joined.strip(" |"):
						return joined

			for nested in value.values():
				preview = self._flatten_preview_value(nested)
				if preview:
					return preview
			return ""

		if isinstance(value, (list, tuple)):
			parts = [self._flatten_preview_value(item) for item in value]
			return " | ".join(part for part in parts if part)

		return str(value) if value is not None else ""

	def _render_pages(self, rows: list[dict[str, str]]) -> list[str]:
		if not rows:
			rows = [{"name": "(none)", "example": "", "output": "No custom commands available."}]

		pages: list[str] = []
		current_lines: list[str] = []

		for row in rows:
			row_lines = self._render_row_lines(row["name"], row["example"], row["output"])
			trial_lines = current_lines + row_lines
			is_first_page = not pages
			trial_page = self._build_page_content(trial_lines, is_first_page=is_first_page)
			if current_lines and len(trial_page) > 1900:
				pages.append(self._build_page_content(current_lines, is_first_page=is_first_page))
				current_lines = row_lines
			else:
				current_lines = trial_lines

		if current_lines or not pages:
			pages.append(self._build_page_content(current_lines, is_first_page=not pages))

		return pages

	def _build_page_content(self, row_lines: list[str], *, is_first_page: bool) -> str:
		prefix = (
			f"{self._TITLE}\n{self._NOTE}\n\n\n"
			if is_first_page
			else f"{self._TITLE} (continued)\n\n"
		)
		header = [
			f"{'Command Name':<{self._NAME_WIDTH}} | {'Example':<{self._EXAMPLE_WIDTH}} | Output",
			f"{'-' * self._NAME_WIDTH}-+-{'-' * self._EXAMPLE_WIDTH}-+-{'-' * self._OUTPUT_WIDTH}",
		]
		table_lines = header + row_lines
		return prefix + "```text\n" + "\n".join(table_lines) + "\n```"

	def _render_row_lines(self, name: str, example: str, output: str) -> list[str]:
		name_lines = self._wrap_cell(name, self._NAME_WIDTH)
		example_lines = self._wrap_cell(example, self._EXAMPLE_WIDTH)
		output_lines = self._wrap_cell(output, self._OUTPUT_WIDTH)
		row_count = max(len(name_lines), len(example_lines), len(output_lines))
		lines: list[str] = []

		for index in range(row_count):
			name_part = name_lines[index] if index < len(name_lines) else ""
			example_part = example_lines[index] if index < len(example_lines) else ""
			output_part = output_lines[index] if index < len(output_lines) else ""
			lines.append(
				f"{name_part:<{self._NAME_WIDTH}} | {example_part:<{self._EXAMPLE_WIDTH}} | {output_part}"
			)

		return lines

	def _wrap_cell(self, value: str, width: int) -> list[str]:
		cleaned = re.sub(r"\s+", " ", value).strip()
		if not cleaned:
			return [""]
		return textwrap.wrap(cleaned, width=width, break_long_words=True, break_on_hyphens=False) or [""]

	async def _publish_pages(self, channel: discord.TextChannel, pages: list[str]) -> None:
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
		for index, page in enumerate(pages):
			if index < len(existing_messages):
				message = existing_messages[index]
				if message.content != page:
					with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
						await message.edit(content=page)
				published_ids.append(message.id)
				continue

			with contextlib.suppress(discord.Forbidden, discord.HTTPException):
				message = await channel.send(page)
				published_ids.append(message.id)

		for message in existing_messages[len(pages):]:
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
