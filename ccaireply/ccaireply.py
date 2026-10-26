from __future__ import annotations

import asyncio
import contextlib
import json
import re
from typing import Any, Optional
from urllib import error, request

import discord
from redbot.core import Config, commands


class CCAIReply(commands.Cog):
	"""AI chatbot replies for mentions and replies."""

	_VALID_PROVIDERS = {"cloud", "onprem", "local"}

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.config = Config.get_conf(self, identifier=853217640113, force_registration=True)
		self.config.register_guild(
			enabled=False,
			trigger_on_mention=True,
			trigger_on_name=True,
			trigger_on_reply=True,
			provider="cloud",
			endpoint_url="",
			api_key="",
			model="",
			system_prompt="You are a helpful Discord assistant.",
			temperature=0.7,
			max_tokens=300,
			timeout_seconds=30,
			extra_headers={},
		)

	@commands.group(invoke_without_command=True)
	@commands.guild_only()
	@commands.admin_or_permissions(manage_guild=True)
	async def ccaireplyset(self, ctx: commands.Context) -> None:
		"""Configure AI chatbot reply behavior and model connection settings."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@ccaireplyset.command(name="enable")
	async def ccaireplyset_enable(self, ctx: commands.Context) -> None:
		"""Enable AI auto-replies in this server."""
		await self.config.guild(ctx.guild).enabled.set(True)
		await ctx.send("CCAIReply enabled.")

	@ccaireplyset.command(name="disable")
	async def ccaireplyset_disable(self, ctx: commands.Context) -> None:
		"""Disable AI auto-replies in this server."""
		await self.config.guild(ctx.guild).enabled.set(False)
		await ctx.send("CCAIReply disabled.")

	@ccaireplyset.command(name="provider")
	async def ccaireplyset_provider(self, ctx: commands.Context, provider: str) -> None:
		"""Set provider mode: cloud, onprem, or local."""
		normalized = provider.casefold().strip()
		if normalized not in self._VALID_PROVIDERS:
			await ctx.send("Provider must be one of: cloud, onprem, local.")
			return
		await self.config.guild(ctx.guild).provider.set(normalized)
		await ctx.send(f"Provider set to `{normalized}`.")

	@ccaireplyset.command(name="endpoint")
	async def ccaireplyset_endpoint(self, ctx: commands.Context, *, endpoint_url: str) -> None:
		"""Set model chat endpoint URL (OpenAI-compatible /chat/completions)."""
		url = endpoint_url.strip()
		await self.config.guild(ctx.guild).endpoint_url.set(url)
		await ctx.send("Endpoint URL updated.")

	@ccaireplyset.command(name="apikey")
	async def ccaireplyset_apikey(self, ctx: commands.Context, *, api_key: str) -> None:
		"""Set API key/token used for Authorization header."""
		await self.config.guild(ctx.guild).api_key.set(api_key.strip())
		await ctx.send("API key updated.")

	@ccaireplyset.command(name="model")
	async def ccaireplyset_model(self, ctx: commands.Context, *, model: str) -> None:
		"""Set model name to use for chat requests."""
		await self.config.guild(ctx.guild).model.set(model.strip())
		await ctx.send(f"Model set to `{model.strip()}`.")

	@ccaireplyset.command(name="system")
	async def ccaireplyset_system(self, ctx: commands.Context, *, system_prompt: str) -> None:
		"""Set system prompt instruction used for AI replies."""
		await self.config.guild(ctx.guild).system_prompt.set(system_prompt.strip())
		await ctx.send("System prompt updated.")

	@ccaireplyset.command(name="temperature")
	async def ccaireplyset_temperature(self, ctx: commands.Context, temperature: float) -> None:
		"""Set sampling temperature (0.0 to 2.0)."""
		temperature = max(0.0, min(2.0, float(temperature)))
		await self.config.guild(ctx.guild).temperature.set(temperature)
		await ctx.send(f"Temperature set to `{temperature:g}`.")

	@ccaireplyset.command(name="maxtokens")
	async def ccaireplyset_maxtokens(self, ctx: commands.Context, max_tokens: int) -> None:
		"""Set max tokens for model replies."""
		max_tokens = max(1, min(4000, int(max_tokens)))
		await self.config.guild(ctx.guild).max_tokens.set(max_tokens)
		await ctx.send(f"Max tokens set to `{max_tokens}`.")

	@ccaireplyset.command(name="timeout")
	async def ccaireplyset_timeout(self, ctx: commands.Context, seconds: int) -> None:
		"""Set HTTP timeout (seconds)."""
		seconds = max(5, min(180, int(seconds)))
		await self.config.guild(ctx.guild).timeout_seconds.set(seconds)
		await ctx.send(f"Timeout set to `{seconds}` seconds.")

	@ccaireplyset.group(name="trigger", invoke_without_command=True)
	async def ccaireplyset_trigger(self, ctx: commands.Context) -> None:
		"""Configure which events trigger auto-replies."""
		if ctx.invoked_subcommand is None:
			await ctx.send_help()

	@ccaireplyset_trigger.command(name="mention")
	async def ccaireplyset_trigger_mention(self, ctx: commands.Context, enabled: bool) -> None:
		await self.config.guild(ctx.guild).trigger_on_mention.set(enabled)
		await ctx.send(f"Trigger on direct mention set to `{enabled}`.")

	@ccaireplyset_trigger.command(name="name")
	async def ccaireplyset_trigger_name(self, ctx: commands.Context, enabled: bool) -> None:
		await self.config.guild(ctx.guild).trigger_on_name.set(enabled)
		await ctx.send(f"Trigger on name mention set to `{enabled}`.")

	@ccaireplyset_trigger.command(name="reply")
	async def ccaireplyset_trigger_reply(self, ctx: commands.Context, enabled: bool) -> None:
		await self.config.guild(ctx.guild).trigger_on_reply.set(enabled)
		await ctx.send(f"Trigger on reply-to-bot set to `{enabled}`.")

	@ccaireplyset.command(name="header")
	async def ccaireplyset_header(self, ctx: commands.Context, header_name: str, *, header_value: str) -> None:
		"""Set or replace a custom HTTP header."""
		key = header_name.strip()
		if not key:
			await ctx.send("Header name is required.")
			return
		async with self.config.guild(ctx.guild).extra_headers() as headers:
			headers[key] = header_value.strip()
		await ctx.send(f"Header `{key}` set.")

	@ccaireplyset.command(name="clearheader")
	async def ccaireplyset_clearheader(self, ctx: commands.Context, *, header_name: str) -> None:
		"""Remove a custom HTTP header."""
		key = header_name.strip()
		removed = False
		async with self.config.guild(ctx.guild).extra_headers() as headers:
			if key in headers:
				headers.pop(key)
				removed = True
		if removed:
			await ctx.send(f"Header `{key}` removed.")
		else:
			await ctx.send("Header not found.")

	@ccaireplyset.command(name="status")
	async def ccaireplyset_status(self, ctx: commands.Context) -> None:
		"""Show current configuration (without secret values)."""
		conf = await self.config.guild(ctx.guild).all()
		headers = conf.get("extra_headers", {})
		endpoint = conf.get("endpoint_url", "")
		model = conf.get("model", "")
		provider = conf.get("provider", "cloud")
		enabled = conf.get("enabled", False)
		trig_mention = conf.get("trigger_on_mention", True)
		trig_name = conf.get("trigger_on_name", True)
		trig_reply = conf.get("trigger_on_reply", True)
		temperature = conf.get("temperature", 0.7)
		max_tokens = conf.get("max_tokens", 300)
		timeout_seconds = conf.get("timeout_seconds", 30)
		api_key_set = bool(conf.get("api_key", "").strip())

		lines = [
			f"- enabled: `{enabled}`",
			f"- provider: `{provider}`",
			f"- endpoint_url: `{endpoint or '(not set)'}`",
			f"- model: `{model or '(not set)'}`",
			f"- api_key_set: `{api_key_set}`",
			f"- triggers: mention=`{trig_mention}`, name=`{trig_name}`, reply=`{trig_reply}`",
			f"- temperature: `{temperature}`",
			f"- max_tokens: `{max_tokens}`",
			f"- timeout_seconds: `{timeout_seconds}`",
			f"- custom_headers: `{', '.join(headers.keys()) if headers else '(none)'}`",
		]
		await ctx.send("CCAIReply configuration:\n" + "\n".join(lines))

	@ccaireplyset.command(name="test")
	async def ccaireplyset_test(self, ctx: commands.Context, *, prompt: str = "Say hello in one short sentence.") -> None:
		"""Send a test prompt to the configured model and show the response."""
		conf = await self.config.guild(ctx.guild).all()
		ok, result = await self._request_model_reply(conf, prompt.strip() or "Say hello.")
		if not ok:
			await ctx.send(f"Test failed: {result}")
			return
		preview = result if len(result) <= 1500 else (result[:1500] + "...")
		await ctx.send("Test succeeded. Model reply:\n" + preview)

	def _message_mentions_bot(self, message: discord.Message) -> bool:
		bot_user = self.bot.user
		if bot_user is None:
			return False
		for mentioned in message.mentions:
			if mentioned.id == bot_user.id:
				return True
		return False

	def _message_mentions_bot_name(self, message: discord.Message) -> bool:
		bot_user = self.bot.user
		if bot_user is None:
			return False
		content = (message.content or "").strip()
		if not content:
			return False

		name_candidates = {bot_user.name.casefold()}
		if isinstance(message.guild, discord.Guild):
			me = message.guild.me
			if me is not None:
				name_candidates.add((me.display_name or "").casefold())

		for name in {n for n in name_candidates if n}:
			pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
			if re.search(pattern, content, flags=re.IGNORECASE):
				return True
		return False

	async def _message_replies_to_bot(self, message: discord.Message) -> bool:
		bot_user = self.bot.user
		if bot_user is None:
			return False
		ref = message.reference
		if ref is None:
			return False
		resolved = ref.resolved
		if isinstance(resolved, discord.Message):
			author = resolved.author
			return author is not None and author.id == bot_user.id
		if ref.message_id is None or message.channel is None:
			return False
		with contextlib.suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
			referenced = await message.channel.fetch_message(ref.message_id)
			return referenced.author is not None and referenced.author.id == bot_user.id
		return False

	async def _should_trigger(self, message: discord.Message, conf: dict[str, Any]) -> bool:
		if conf.get("trigger_on_mention", True) and self._message_mentions_bot(message):
			return True
		if conf.get("trigger_on_name", True) and self._message_mentions_bot_name(message):
			return True
		if conf.get("trigger_on_reply", True) and await self._message_replies_to_bot(message):
			return True
		return False

	def _build_chat_payload(self, conf: dict[str, Any], user_message: str) -> dict[str, Any]:
		system_prompt = str(conf.get("system_prompt", "") or "").strip()
		model = str(conf.get("model", "") or "").strip()
		temperature = float(conf.get("temperature", 0.7) or 0.7)
		max_tokens = int(conf.get("max_tokens", 300) or 300)
		messages = []
		if system_prompt:
			messages.append({"role": "system", "content": system_prompt})
		messages.append({"role": "user", "content": user_message})
		payload: dict[str, Any] = {
			"messages": messages,
			"temperature": temperature,
			"max_tokens": max_tokens,
		}
		if model:
			payload["model"] = model
		return payload

	def _extract_reply_text(self, response_json: dict[str, Any]) -> str:
		choices = response_json.get("choices")
		if isinstance(choices, list) and choices:
			first = choices[0]
			if isinstance(first, dict):
				message = first.get("message")
				if isinstance(message, dict):
					content = message.get("content")
					if isinstance(content, str) and content.strip():
						return content.strip()
				text = first.get("text")
				if isinstance(text, str) and text.strip():
					return text.strip()
		content = response_json.get("content")
		if isinstance(content, str) and content.strip():
			return content.strip()
		return ""

	def _perform_http_chat_request(self, conf: dict[str, Any], payload: dict[str, Any]) -> tuple[bool, str]:
		endpoint_url = str(conf.get("endpoint_url", "") or "").strip()
		if not endpoint_url:
			return False, "Endpoint URL is not configured."

		api_key = str(conf.get("api_key", "") or "").strip()
		extra_headers = conf.get("extra_headers", {})
		headers = {
			"Content-Type": "application/json",
		}
		if api_key:
			headers["Authorization"] = f"Bearer {api_key}"
		if isinstance(extra_headers, dict):
			for key, value in extra_headers.items():
				if isinstance(key, str) and key.strip():
					headers[key.strip()] = str(value)

		body = json.dumps(payload).encode("utf-8")
		timeout_seconds = int(conf.get("timeout_seconds", 30) or 30)
		http_request = request.Request(endpoint_url, data=body, headers=headers, method="POST")
		try:
			with request.urlopen(http_request, timeout=timeout_seconds) as resp:
				response_text = resp.read().decode("utf-8", errors="replace")
		except error.HTTPError as exc:
			error_text = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
			return False, f"HTTP error {exc.code}: {error_text[:500]}"
		except Exception as exc:
			return False, f"Request failed: {exc}"

		try:
			response_json = json.loads(response_text)
		except json.JSONDecodeError:
			return False, "Model response was not valid JSON."

		reply_text = self._extract_reply_text(response_json)
		if not reply_text:
			return False, "Model response did not include reply content."
		return True, reply_text

	async def _request_model_reply(self, conf: dict[str, Any], user_message: str) -> tuple[bool, str]:
		payload = self._build_chat_payload(conf, user_message)
		return await asyncio.to_thread(self._perform_http_chat_request, conf, payload)

	def _chunk_text(self, text: str, max_len: int = 1900) -> list[str]:
		content = text.strip()
		if not content:
			return []
		chunks: list[str] = []
		while len(content) > max_len:
			split_at = content.rfind("\n", 0, max_len)
			if split_at <= 0:
				split_at = content.rfind(" ", 0, max_len)
			if split_at <= 0:
				split_at = max_len
			chunks.append(content[:split_at].rstrip())
			content = content[split_at:].lstrip()
		if content:
			chunks.append(content)
		return chunks

	async def _send_ai_reply(self, message: discord.Message, reply_text: str) -> None:
		parts = self._chunk_text(reply_text)
		if not parts:
			return
		first = True
		for part in parts:
			with contextlib.suppress(discord.Forbidden, discord.HTTPException):
				if first:
					await message.reply(part, mention_author=False)
					first = False
				else:
					await message.channel.send(part)

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message) -> None:
		if message.guild is None or message.author.bot:
			return
		conf = await self.config.guild(message.guild).all()
		if not conf.get("enabled", False):
			return
		should_trigger = await self._should_trigger(message, conf)
		if not should_trigger:
			return
		user_prompt = (message.content or "").strip()
		if not user_prompt:
			user_prompt = "(No text provided)"
		ok, result = await self._request_model_reply(conf, user_prompt)
		if not ok:
			with contextlib.suppress(discord.Forbidden, discord.HTTPException):
				await message.reply(f"AI request failed: {result}", mention_author=False)
			return
		await self._send_ai_reply(message, result)


async def setup(bot: commands.Bot):
	await bot.add_cog(CCAIReply(bot))

