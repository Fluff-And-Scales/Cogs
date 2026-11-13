from __future__ import annotations

from datetime import datetime

import discord
from redbot.core import Config, commands


class CCQuotes(commands.Cog):
	"""Submit formatted quotes to a configured channel."""

	_VALID_SIZE_MODES = {"normal", "large", "xlarge", "xxlarge"}

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.config = Config.get_conf(self, identifier=853217640116, force_registration=True)
		self.config.register_guild(quote_channel_id=None, quote_size_mode="normal")

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	async def quote(self, ctx: commands.Context) -> None:
		"""Quote commands."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	async def _build_quote_embed(
		self,
		submitter_name: str,
		quote_text: str,
		said_by: str | None = None,
		date_said: str | None = None,
		quote_size_mode: str = "normal",
	) -> discord.Embed:
		clean_quote = quote_text.strip()
		clean_said_by = said_by.strip() if isinstance(said_by, str) and said_by.strip() else None
		clean_date = date_said.strip() if isinstance(date_said, str) and date_said.strip() else datetime.utcnow().strftime("%m/%d/%Y %H:%M")
		if quote_size_mode not in self._VALID_SIZE_MODES:
			quote_size_mode = "normal"

		quote_line = f'**"{clean_quote}"**'
		if quote_size_mode == "large":
			quote_line = f'### {quote_line}'
		elif quote_size_mode == "xlarge":
			quote_line = f'## {quote_line}'
		elif quote_size_mode == "xxlarge":
			quote_line = f'# {quote_line}'

		body_lines = [
			f"User **{submitter_name}** has indicated a new quote! Entry added to registry.",
			"",
			quote_line,
		]
		if clean_said_by or clean_date:
			body_lines.append("** **")
			body_lines.append("** **")
		if clean_said_by:
			body_lines.append(f"- {clean_said_by},")
		if clean_date:
			date_indent = "  " if clean_said_by else ""
			body_lines.append(f"{date_indent}{clean_date}")

		return discord.Embed(
			title="# New Quote Detected",
			description="\n".join(body_lines),
			color=discord.Color.teal(),
		)

	@quote.command(name="add")
	async def quote_add(
		self,
		ctx: commands.Context,
		quote_text: str,
		said_by: str | None = None,
		date_said: str | None = None,
	) -> None:
		"""Submit a quote: quote add <quote_text> [said_by] [date_said]."""
		channel_id = await self.config.guild(ctx.guild).quote_channel_id()
		if not isinstance(channel_id, int):
			await ctx.send("Quote channel is not set. Use `quoteset channel #channel` first.")
			return

		channel = ctx.guild.get_channel(channel_id)
		if not isinstance(channel, discord.TextChannel):
			await ctx.send("Configured quote channel is not available. Set it again with `quoteset channel #channel`.")
			return

		clean_quote = quote_text.strip()
		if not clean_quote:
			await ctx.send("Quote text is required.")
			return

		quote_size_mode = str(await self.config.guild(ctx.guild).quote_size_mode() or "normal").casefold().strip()
		embed = await self._build_quote_embed(
			submitter_name=ctx.author.display_name,
			quote_text=clean_quote,
			said_by=said_by,
			date_said=date_said,
			quote_size_mode=quote_size_mode,
		)

		await channel.send(embed=embed)
		await ctx.send(f"Quote posted to {channel.mention}.")

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	@commands.admin_or_permissions(manage_guild=True)
	async def quoteset(self, ctx: commands.Context) -> None:
		"""Configure CCQuotes settings."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@quoteset.command(name="channel")
	async def quoteset_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
		"""Set the quotes channel."""
		await self.config.guild(ctx.guild).quote_channel_id.set(channel.id)
		await ctx.send(f"Quotes channel set to {channel.mention}.")

	@quoteset.command(name="fontsize")
	async def quoteset_fontsize(self, ctx: commands.Context, size: str) -> None:
		"""Set quote size style: normal, large, xlarge, or xxlarge."""
		normalized = size.casefold().strip()
		if normalized not in self._VALID_SIZE_MODES:
			await ctx.send("Font size must be `normal`, `large`, `xlarge`, or `xxlarge`.")
			return
		await self.config.guild(ctx.guild).quote_size_mode.set(normalized)
		await ctx.send(f"Quote font size style set to `{normalized}`.")

	@quoteset.command(name="show")
	async def quoteset_show(self, ctx: commands.Context) -> None:
		"""Show the current quotes channel and quote size mode."""
		channel_id = await self.config.guild(ctx.guild).quote_channel_id()
		channel = ctx.guild.get_channel(channel_id) if isinstance(channel_id, int) else None
		size_mode = str(await self.config.guild(ctx.guild).quote_size_mode() or "normal").casefold().strip()
		if size_mode not in self._VALID_SIZE_MODES:
			size_mode = "normal"
		if isinstance(channel, discord.TextChannel):
			await ctx.send(f"Current quotes channel: {channel.mention}\nQuote size style: `{size_mode}`")
		else:
			await ctx.send(f"Quotes channel is not set.\nQuote size style: `{size_mode}`")

	@quote.command(name="preview")
	async def quote_preview(
		self,
		ctx: commands.Context,
		quote_text: str,
		said_by: str | None = None,
		date_said: str | None = None,
	) -> None:
		"""Preview how a quote embed will look using current settings in this channel."""
		clean_quote = quote_text.strip()
		if not clean_quote:
			await ctx.send("Quote text is required.")
			return

		quote_size_mode = str(await self.config.guild(ctx.guild).quote_size_mode() or "normal").casefold().strip()
		embed = await self._build_quote_embed(
			submitter_name=ctx.author.display_name,
			quote_text=clean_quote,
			said_by=said_by,
			date_said=date_said,
			quote_size_mode=quote_size_mode,
		)
		await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
	await bot.add_cog(CCQuotes(bot))
