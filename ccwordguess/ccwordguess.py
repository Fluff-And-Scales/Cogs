from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Optional

import discord
from redbot.core import Config, commands


@dataclass
class GameState:
	word: str
	display_word: str
	attempts_left: int
	guessed_letters: set[str]
	guessed_words: set[str]
	started_by: int


class CCWordGuess(commands.Cog):
	"""A simple channel-based word guessing game."""

	_MAX_WORD_LEN = 32

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.config = Config.get_conf(self, identifier=853217640115, force_registration=True)
		self.config.register_guild(
			enabled=True,
			default_attempts=8,
			allow_any_channel=False,
			allowed_channel_ids=[],
			word_bank=["python", "discord", "redbot", "cog", "automation", "channel", "message", "trigger"],
			scores={},
		)
		self._games: dict[tuple[int, int], GameState] = {}

	def _normalize_word(self, value: str) -> str:
		return re.sub(r"[^a-z]", "", value.casefold())

	def _build_display_word(self, word: str, guessed_letters: set[str]) -> str:
		return " ".join(ch if ch in guessed_letters else "_" for ch in word)

	def _select_word(self, words: list[str]) -> Optional[str]:
		cleaned = [self._normalize_word(w) for w in words]
		cleaned = [w for w in cleaned if 1 <= len(w) <= self._MAX_WORD_LEN]
		if not cleaned:
			return None
		return random.choice(cleaned)

	def _game_key(self, guild_id: int, channel_id: int) -> tuple[int, int]:
		return guild_id, channel_id

	async def _award_point(self, guild: discord.Guild, user_id: int) -> int:
		new_score = 1
		async with self.config.guild(guild).scores() as scores:
			key = str(user_id)
			current = int(scores.get(key, 0) or 0)
			new_score = current + 1
			scores[key] = new_score
		return new_score

	async def _channel_allowed(self, guild_conf, channel_id: int) -> bool:
		if await guild_conf.allow_any_channel():
			return True
		allowed = await guild_conf.allowed_channel_ids()
		return channel_id in [cid for cid in allowed if isinstance(cid, int)]

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	async def ccwordguess(self, ctx: commands.Context) -> None:
		"""Word guessing game commands."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@ccwordguess.command(name="start")
	async def ccwordguess_start(self, ctx: commands.Context) -> None:
		"""Start a new word guessing game in this channel."""
		guild_conf = self.config.guild(ctx.guild)
		if not await guild_conf.enabled():
			await ctx.send("CCWordGuess is disabled in this server.")
			return

		if not await self._channel_allowed(guild_conf, ctx.channel.id):
			await ctx.send("Word guessing is not enabled in this channel.")
			return

		key = self._game_key(ctx.guild.id, ctx.channel.id)
		if key in self._games:
			await ctx.send("A game is already active in this channel.")
			return

		word_bank = await guild_conf.word_bank()
		word = self._select_word(word_bank)
		if not word:
			await ctx.send("No valid words configured in the word bank.")
			return

		attempts = max(1, int(await guild_conf.default_attempts()))
		guessed_letters: set[str] = set()
		display = self._build_display_word(word, guessed_letters)
		self._games[key] = GameState(
			word=word,
			display_word=display,
			attempts_left=attempts,
			guessed_letters=guessed_letters,
			guessed_words=set(),
			started_by=ctx.author.id,
		)
		await ctx.send(
			f"Word Guess started. Word: `{display}` | Attempts left: `{attempts}`\n"
			"Use `ccwordguess guess <letter_or_word>` to play."
		)

	@ccwordguess.command(name="guess")
	async def ccwordguess_guess(self, ctx: commands.Context, *, guess: str) -> None:
		"""Guess a letter or the full word."""
		key = self._game_key(ctx.guild.id, ctx.channel.id)
		game = self._games.get(key)
		if game is None:
			await ctx.send("No active game in this channel. Start one with `ccwordguess start`.")
			return

		normalized_guess = self._normalize_word(guess)
		if not normalized_guess:
			await ctx.send("Please guess a letter or word using alphabetic characters.")
			return

		if len(normalized_guess) == 1:
			letter = normalized_guess
			if letter in game.guessed_letters:
				await ctx.send(f"`{letter}` was already guessed.")
				return
			game.guessed_letters.add(letter)
			if letter not in game.word:
				game.attempts_left -= 1
			else:
				game.display_word = self._build_display_word(game.word, game.guessed_letters)
		else:
			if normalized_guess in game.guessed_words:
				await ctx.send(f"`{normalized_guess}` was already guessed.")
				return
			game.guessed_words.add(normalized_guess)
			if normalized_guess == game.word:
				game.display_word = " ".join(game.word)
				score = await self._award_point(ctx.guild, ctx.author.id)
				await ctx.send(f"Correct! {ctx.author.mention} solved it: `{game.word}` | Score: `{score}`")
				self._games.pop(key, None)
				return
			game.attempts_left -= 1

		if "_" not in game.display_word.replace(" ", ""):
			score = await self._award_point(ctx.guild, ctx.author.id)
			await ctx.send(f"Nice! {ctx.author.mention} completed the word: `{game.word}` | Score: `{score}`")
			self._games.pop(key, None)
			return

		if game.attempts_left <= 0:
			await ctx.send(f"Game over. The word was `{game.word}`.")
			self._games.pop(key, None)
			return

		await ctx.send(
			f"Word: `{game.display_word}` | Attempts left: `{game.attempts_left}`"
		)

	@ccwordguess.command(name="stop")
	async def ccwordguess_stop(self, ctx: commands.Context) -> None:
		"""Stop the active game in this channel."""
		key = self._game_key(ctx.guild.id, ctx.channel.id)
		if key not in self._games:
			await ctx.send("No active game in this channel.")
			return
		self._games.pop(key, None)
		await ctx.send("Word Guess game stopped.")

	@ccwordguess.command(name="status")
	async def ccwordguess_status(self, ctx: commands.Context) -> None:
		"""Show the current game status in this channel."""
		key = self._game_key(ctx.guild.id, ctx.channel.id)
		game = self._games.get(key)
		if game is None:
			await ctx.send("No active game in this channel.")
			return
		await ctx.send(
			f"Word: `{game.display_word}` | Attempts left: `{game.attempts_left}` | "
			f"Guessed letters: `{', '.join(sorted(game.guessed_letters)) or 'none'}`"
		)

	@ccwordguess.command(name="score")
	async def ccwordguess_score(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
		"""Show a user's score (defaults to yourself)."""
		target = user or ctx.author
		scores = await self.config.guild(ctx.guild).scores()
		score = int(scores.get(str(target.id), 0) or 0)
		await ctx.send(f"{target.mention} score: `{score}`")

	@ccwordguess.command(name="leaderboard")
	async def ccwordguess_leaderboard(self, ctx: commands.Context) -> None:
		"""Show top players by score."""
		scores = await self.config.guild(ctx.guild).scores()
		entries: list[tuple[int, int]] = []
		for user_id_str, score_value in scores.items():
			try:
				user_id = int(user_id_str)
				score = int(score_value)
			except (TypeError, ValueError):
				continue
			if score > 0:
				entries.append((user_id, score))

		if not entries:
			await ctx.send("No scores yet.")
			return

		entries.sort(key=lambda x: x[1], reverse=True)
		lines = []
		for rank, (user_id, score) in enumerate(entries[:10], start=1):
			member = ctx.guild.get_member(user_id)
			name = member.mention if member is not None else f"UserID {user_id}"
			lines.append(f"{rank}. {name} - `{score}`")
		await ctx.send("CCWordGuess leaderboard:\n" + "\n".join(lines))

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	@commands.admin_or_permissions(manage_guild=True)
	async def ccwordguessset(self, ctx: commands.Context) -> None:
		"""Configure CCWordGuess settings."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@ccwordguessset.command(name="enable")
	async def ccwordguessset_enable(self, ctx: commands.Context) -> None:
		await self.config.guild(ctx.guild).enabled.set(True)
		await ctx.send("CCWordGuess enabled.")

	@ccwordguessset.command(name="disable")
	async def ccwordguessset_disable(self, ctx: commands.Context) -> None:
		await self.config.guild(ctx.guild).enabled.set(False)
		await ctx.send("CCWordGuess disabled.")

	@ccwordguessset.command(name="attempts")
	async def ccwordguessset_attempts(self, ctx: commands.Context, attempts: int) -> None:
		attempts = max(1, min(26, int(attempts)))
		await self.config.guild(ctx.guild).default_attempts.set(attempts)
		await ctx.send(f"Default attempts set to {attempts}.")

	@ccwordguessset.command(name="allowanychannel")
	async def ccwordguessset_allowanychannel(self, ctx: commands.Context, enabled: bool) -> None:
		await self.config.guild(ctx.guild).allow_any_channel.set(enabled)
		await ctx.send(f"Allow any channel set to `{enabled}`.")

	@ccwordguessset.group(name="channels", invoke_without_command=True)
	async def ccwordguessset_channels(self, ctx: commands.Context) -> None:
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@ccwordguessset_channels.command(name="add")
	async def ccwordguessset_channels_add(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
		async with self.config.guild(ctx.guild).allowed_channel_ids() as allowed:
			if channel.id not in allowed:
				allowed.append(channel.id)
		await ctx.send(f"Added {channel.mention} to allowed channels.")

	@ccwordguessset_channels.command(name="remove")
	async def ccwordguessset_channels_remove(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
		removed = False
		async with self.config.guild(ctx.guild).allowed_channel_ids() as allowed:
			if channel.id in allowed:
				allowed.remove(channel.id)
				removed = True
		if removed:
			await ctx.send(f"Removed {channel.mention} from allowed channels.")
		else:
			await ctx.send("Channel was not in allowed list.")

	@ccwordguessset_channels.command(name="list")
	async def ccwordguessset_channels_list(self, ctx: commands.Context) -> None:
		allowed = await self.config.guild(ctx.guild).allowed_channel_ids()
		if not allowed:
			await ctx.send("No channel restrictions configured.")
			return
		lines = []
		for channel_id in allowed:
			channel = ctx.guild.get_channel(channel_id) if isinstance(channel_id, int) else None
			lines.append(channel.mention if isinstance(channel, discord.TextChannel) else f"#{channel_id}")
		await ctx.send("Allowed channels:\n- " + "\n- ".join(lines))

	@ccwordguessset.command(name="addword")
	async def ccwordguessset_addword(self, ctx: commands.Context, *, word: str) -> None:
		normalized = self._normalize_word(word)
		if not normalized:
			await ctx.send("Please provide a valid alphabetic word.")
			return
		if len(normalized) > self._MAX_WORD_LEN:
			await ctx.send(f"Word too long. Max length is {self._MAX_WORD_LEN}.")
			return
		async with self.config.guild(ctx.guild).word_bank() as word_bank:
			if normalized not in word_bank:
				word_bank.append(normalized)
		await ctx.send(f"Added word `{normalized}`.")

	@ccwordguessset.command(name="removeword")
	async def ccwordguessset_removeword(self, ctx: commands.Context, *, word: str) -> None:
		normalized = self._normalize_word(word)
		removed = False
		async with self.config.guild(ctx.guild).word_bank() as word_bank:
			if normalized in word_bank:
				word_bank.remove(normalized)
				removed = True
		if removed:
			await ctx.send(f"Removed word `{normalized}`.")
		else:
			await ctx.send("Word not found in bank.")

	@ccwordguessset.command(name="listwords")
	async def ccwordguessset_listwords(self, ctx: commands.Context) -> None:
		words = await self.config.guild(ctx.guild).word_bank()
		if not words:
			await ctx.send("Word bank is empty.")
			return
		preview = ", ".join(sorted(words)[:60])
		if len(words) > 60:
			preview += ", ..."
		await ctx.send(f"Word bank ({len(words)} words):\n{preview}")

	@ccwordguessset.command(name="resetscores")
	async def ccwordguessset_resetscores(self, ctx: commands.Context) -> None:
		"""Reset all word guess scores in this server."""
		await self.config.guild(ctx.guild).scores.set({})
		await ctx.send("CCWordGuess scores reset.")


async def setup(bot: commands.Bot):
	await bot.add_cog(CCWordGuess(bot))
