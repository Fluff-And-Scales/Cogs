from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Optional

import discord
from redbot.core import Config, commands


@dataclass
class GameState:
	word: str
	attempts_left: int
	guesses: list[str] = field(default_factory=list)


class CCWordGuess(commands.Cog):
	"""A Wordle-style private word guessing game."""

	_WORD_LENGTH = 5
	_DEFAULT_ATTEMPTS = 6
	_FEEDBACK_CORRECT = "🟩"
	_FEEDBACK_PRESENT = "🟨"
	_FEEDBACK_ABSENT = "⬛"
	_FALLBACK_DELETE_AFTER_SECONDS = 30

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.config = Config.get_conf(self, identifier=853217640115, force_registration=True)
		self.config.register_guild(
			enabled=True,
			default_attempts=self._DEFAULT_ATTEMPTS,
			allow_any_channel=False,
			allowed_channel_ids=[],
			word_bank=["apple", "beach", "crown", "dream", "flame", "grape", "light", "stone", "track", "world"],
			scores={},
			progress_index={},
		)
		self._games: dict[tuple[int, int], GameState] = {}

	def _normalize_word(self, value: str) -> str:
		return re.sub(r"[^a-z]", "", value.casefold())

	def _normalize_wordle_word(self, value: str) -> Optional[str]:
		normalized = self._normalize_word(value)
		if len(normalized) != self._WORD_LENGTH:
			return None
		return normalized

	def _game_key(self, guild_id: int, user_id: int) -> tuple[int, int]:
		return guild_id, user_id

	async def _channel_allowed(self, guild_conf, channel_id: int) -> bool:
		if await guild_conf.allow_any_channel():
			return True
		allowed = await guild_conf.allowed_channel_ids()
		return channel_id in [cid for cid in allowed if isinstance(cid, int)]

	async def _send_private(self, ctx: commands.Context, text: str) -> bool:
		interaction = getattr(ctx, "interaction", None)
		try:
			if interaction is not None:
				await ctx.send(text, ephemeral=True)
			else:
				await ctx.send(
					f"{ctx.author.mention} {text}",
					delete_after=self._FALLBACK_DELETE_AFTER_SECONDS,
				)
			return True
		except (discord.Forbidden, discord.HTTPException):
			return False

	def _generate_word(self, used_words: set[str]) -> str:
		consonants = "bcdfghjklmnpqrstvwxyz"
		vowels = "aeiou"
		for _ in range(200):
			candidate = "".join(
				random.choice(consonants if i % 2 == 0 else vowels) for i in range(self._WORD_LENGTH)
			)
			if candidate not in used_words:
				return candidate
		return f"w{random.randint(1000, 9999)}"[: self._WORD_LENGTH]

	async def _ensure_word_for_index(self, guild: discord.Guild, index: int) -> str:
		guild_conf = self.config.guild(guild)
		async with guild_conf.word_bank() as word_bank:
			normalized_bank = []
			for word in word_bank:
				normalized = self._normalize_wordle_word(str(word))
				if normalized:
					normalized_bank.append(normalized)
			word_bank[:] = normalized_bank
			used_words = set(normalized_bank)
			while len(word_bank) <= index:
				new_word = self._generate_word(used_words)
				used_words.add(new_word)
				word_bank.append(new_word)
			return word_bank[index]

	def _build_feedback(self, target: str, guess: str) -> str:
		feedback = [self._FEEDBACK_ABSENT] * len(target)
		remaining_counts: dict[str, int] = {}
		for idx, char in enumerate(target):
			if guess[idx] == char:
				feedback[idx] = self._FEEDBACK_CORRECT
			else:
				remaining_counts[char] = remaining_counts.get(char, 0) + 1
		for idx, char in enumerate(guess):
			if feedback[idx] == self._FEEDBACK_CORRECT:
				continue
			if remaining_counts.get(char, 0) > 0:
				feedback[idx] = self._FEEDBACK_PRESENT
				remaining_counts[char] -= 1
		return "".join(feedback)

	async def _award_point(self, guild: discord.Guild, user_id: int) -> int:
		new_score = 1
		async with self.config.guild(guild).scores() as scores:
			key = str(user_id)
			current = int(scores.get(key, 0) or 0)
			new_score = current + 1
			scores[key] = new_score
		return new_score

	async def _get_user_progress_index(self, guild: discord.Guild, user_id: int) -> int:
		progress = await self.config.guild(guild).progress_index()
		return max(0, int(progress.get(str(user_id), 0) or 0))

	async def _set_user_progress_index(self, guild: discord.Guild, user_id: int, index: int) -> None:
		async with self.config.guild(guild).progress_index() as progress:
			progress[str(user_id)] = max(0, int(index))

	async def _start_or_restart_game(self, ctx: commands.Context) -> bool:
		index = await self._get_user_progress_index(ctx.guild, ctx.author.id)
		word = await self._ensure_word_for_index(ctx.guild, index)
		attempts = max(1, int(await self.config.guild(ctx.guild).default_attempts()))
		self._games[self._game_key(ctx.guild.id, ctx.author.id)] = GameState(word=word, attempts_left=attempts)
		return await self._send_private(
			ctx,
			f"Wordle game started. Guess a `{self._WORD_LENGTH}`-letter word. Attempts: `{attempts}`\n"
			"Use `/ccwordguess guess <word>` (or prefix command) to play.",
		)

	def _render_board(self, game: GameState) -> str:
		if not game.guesses:
			return "(no guesses yet)"
		lines = []
		for guess in game.guesses:
			feedback = self._build_feedback(game.word, guess)
			lines.append(f"`{guess}` {feedback}")
		return "\n".join(lines)

	@commands.hybrid_group(invoke_without_command=True)
	@commands.guild_only()
	async def ccwordguess(self, ctx: commands.Context) -> None:
		"""Wordle-style game commands (responses are private when used as slash commands)."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@ccwordguess.command(name="start")
	async def ccwordguess_start(self, ctx: commands.Context) -> None:
		"""Start your private Wordle game."""
		guild_conf = self.config.guild(ctx.guild)
		if not await guild_conf.enabled():
			await ctx.send("CCWordGuess is disabled in this server.")
			return
		if not await self._channel_allowed(guild_conf, ctx.channel.id):
			await ctx.send("Word guessing is not enabled in this channel.")
			return
		await self._start_or_restart_game(ctx)

	@ccwordguess.command(name="guess")
	async def ccwordguess_guess(self, ctx: commands.Context, *, guess: str) -> None:
		"""Guess a full word (Wordle style)."""
		key = self._game_key(ctx.guild.id, ctx.author.id)
		game = self._games.get(key)
		if game is None:
			ok = await self._send_private(ctx, "No active game. Starting one now...")
			if not ok:
				return
			started = await self._start_or_restart_game(ctx)
			if not started:
				return
			game = self._games.get(key)
			if game is None:
				return

		normalized_guess = self._normalize_wordle_word(guess)
		if normalized_guess is None:
			await self._send_private(ctx, f"Please guess exactly `{self._WORD_LENGTH}` letters.")
			return
		if normalized_guess in game.guesses:
			await self._send_private(ctx, f"You already guessed `{normalized_guess}`.")
			return

		game.guesses.append(normalized_guess)
		game.attempts_left -= 1
		feedback = self._build_feedback(game.word, normalized_guess)

		if normalized_guess == game.word:
			score = await self._award_point(ctx.guild, ctx.author.id)
			current_index = await self._get_user_progress_index(ctx.guild, ctx.author.id)
			next_index = current_index + 1
			await self._set_user_progress_index(ctx.guild, ctx.author.id, next_index)
			next_word = await self._ensure_word_for_index(ctx.guild, next_index)
			attempts = max(1, int(await self.config.guild(ctx.guild).default_attempts()))
			self._games[key] = GameState(word=next_word, attempts_left=attempts)
			await self._send_private(
				ctx,
				f"`{normalized_guess}` {feedback}\n"
				f"Correct! Score: `{score}`\n"
				f"Next word loaded automatically. Guess a new `{self._WORD_LENGTH}`-letter word. Attempts: `{attempts}`",
			)
			return

		if game.attempts_left <= 0:
			self._games.pop(key, None)
			await self._send_private(
				ctx,
				f"`{normalized_guess}` {feedback}\nGame over. The word was `{game.word}`.\nUse `ccwordguess start` to try again.",
			)
			return

		await self._send_private(
			ctx,
			f"`{normalized_guess}` {feedback}\nAttempts left: `{game.attempts_left}`\nBoard:\n{self._render_board(game)}",
		)

	@ccwordguess.command(name="stop")
	async def ccwordguess_stop(self, ctx: commands.Context) -> None:
		"""Stop your active private game."""
		key = self._game_key(ctx.guild.id, ctx.author.id)
		if key not in self._games:
			await self._send_private(ctx, "You have no active game.")
			return
		self._games.pop(key, None)
		await self._send_private(ctx, "Your Wordle game was stopped.")

	@ccwordguess.command(name="status")
	async def ccwordguess_status(self, ctx: commands.Context) -> None:
		"""Show your private current game status."""
		key = self._game_key(ctx.guild.id, ctx.author.id)
		game = self._games.get(key)
		if game is None:
			await self._send_private(ctx, "You have no active game.")
			return
		await self._send_private(
			ctx,
			f"Attempts left: `{game.attempts_left}`\nBoard:\n{self._render_board(game)}",
		)

	@ccwordguess.command(name="score")
	async def ccwordguess_score(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
		"""Show a user's score (defaults to yourself)."""
		target = user or ctx.author
		scores = await self.config.guild(ctx.guild).scores()
		score = int(scores.get(str(target.id), 0) or 0)
		await self._send_private(ctx, f"{target.display_name} score: `{score}`")

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
			await self._send_private(ctx, "No scores yet.")
			return
		entries.sort(key=lambda x: x[1], reverse=True)
		lines = []
		for rank, (user_id, score) in enumerate(entries[:10], start=1):
			member = ctx.guild.get_member(user_id)
			name = member.display_name if member is not None else f"UserID {user_id}"
			lines.append(f"{rank}. {name} - `{score}`")
		await self._send_private(ctx, "CCWordGuess leaderboard:\n" + "\n".join(lines))

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
		attempts = max(1, min(12, int(attempts)))
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
		normalized = self._normalize_wordle_word(word)
		if not normalized:
			await ctx.send(f"Please provide a valid `{self._WORD_LENGTH}`-letter word.")
			return
		async with self.config.guild(ctx.guild).word_bank() as word_bank:
			if normalized not in word_bank:
				word_bank.append(normalized)
		await ctx.send(f"Added word `{normalized}`.")

	@ccwordguessset.command(name="removeword")
	async def ccwordguessset_removeword(self, ctx: commands.Context, *, word: str) -> None:
		normalized = self._normalize_wordle_word(word)
		if not normalized:
			await ctx.send(f"Please provide a valid `{self._WORD_LENGTH}`-letter word.")
			return
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
		preview = ", ".join(words[:80])
		if len(words) > 80:
			preview += ", ..."
		await ctx.send(f"Word bank ({len(words)} words):\n{preview}")

	@ccwordguessset.command(name="resetscores")
	async def ccwordguessset_resetscores(self, ctx: commands.Context) -> None:
		await self.config.guild(ctx.guild).scores.set({})
		await ctx.send("CCWordGuess scores reset.")

	@ccwordguessset.command(name="resetprogress")
	async def ccwordguessset_resetprogress(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
		"""Reset progression index for one user or everyone."""
		if user is None:
			await self.config.guild(ctx.guild).progress_index.set({})
			await ctx.send("Progress reset for all users.")
			return
		async with self.config.guild(ctx.guild).progress_index() as progress:
			progress[str(user.id)] = 0
		await ctx.send(f"Progress reset for {user.mention}.")


async def setup(bot: commands.Bot):
	await bot.add_cog(CCWordGuess(bot))
