from __future__ import annotations

import asyncio
import contextlib
import functools
from typing import Any, Optional

import discord
from redbot.core import commands


class ClearCCInvoc(commands.Cog):
    """Delete custom command invocations before the response is sent."""

    _CUSTOMCOM_COG_NAMES = ("CustomCom", "CustomCommands", "customcom")
    _PATCHED_METHOD_NAMES = (
        "on_message_without_command",
        "on_message",
        "check_cc",
        "check_ccs",
        "process_cc",
        "handle_cc",
        "send_cc",
        "send_response",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._patch_task: Optional[asyncio.Task] = None
        self._patched_cog: Optional[Any] = None
        self._patched_method_name: Optional[str] = None
        self._original_method: Optional[Any] = None
        self._patched_listener_event: Optional[str] = None
        self._patched_listener_index: Optional[int] = None
        self._original_listener: Optional[Any] = None

    async def cog_load(self) -> None:
        return

    async def cog_unload(self) -> None:
        if self._patch_task and not self._patch_task.done():
            self._patch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._patch_task
        await self._unpatch_customcom()

    def _get_customcom_cog(self) -> Optional[Any]:
        for name in self._CUSTOMCOM_COG_NAMES:
            cog = self.bot.get_cog(name)
            if cog is not None:
                return cog
        return None

    async def _patch_customcom(self) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            if hasattr(self.bot, "wait_until_red_ready"):
                await asyncio.wait_for(self.bot.wait_until_red_ready(), timeout=30)
            else:
                await asyncio.wait_for(self.bot.wait_until_ready(), timeout=30)

        customcom = None
        for _ in range(60):
            customcom = self._get_customcom_cog()
            if customcom is not None:
                break
            await asyncio.sleep(1)

        if customcom is None:
            return

        for method_name in self._PATCHED_METHOD_NAMES:
            original = getattr(customcom, method_name, None)
            if original is None or not asyncio.iscoroutinefunction(original):
                continue

            wrapped = self._build_wrapper(original)
            setattr(customcom, method_name, wrapped)
            self._replace_registered_listener(customcom, method_name, wrapped)
            self._patched_cog = customcom
            self._patched_method_name = method_name
            self._original_method = original
            return

    def _replace_registered_listener(self, customcom: Any, method_name: str, wrapped: Any) -> None:
        listeners = getattr(self.bot, "extra_events", {}).get(method_name)
        if not listeners:
            return

        for index, listener in enumerate(listeners):
            if getattr(listener, "__self__", None) is customcom and getattr(listener, "__name__", None) == method_name:
                listeners[index] = wrapped
                self._patched_listener_event = method_name
                self._patched_listener_index = index
                self._original_listener = listener
                return

    async def _unpatch_customcom(self) -> None:
        if self._patched_cog is not None and self._patched_method_name is not None and self._original_method is not None:
            setattr(self._patched_cog, self._patched_method_name, self._original_method)

        if (
            self._patched_listener_event is not None
            and self._patched_listener_index is not None
            and self._original_listener is not None
        ):
            listeners = getattr(self.bot, "extra_events", {}).get(self._patched_listener_event)
            if listeners and 0 <= self._patched_listener_index < len(listeners):
                listeners[self._patched_listener_index] = self._original_listener

        self._patched_cog = None
        self._patched_method_name = None
        self._original_method = None
        self._patched_listener_event = None
        self._patched_listener_index = None
        self._original_listener = None

    def _build_wrapper(self, original: Any):
        @functools.wraps(original)
        async def wrapped(*args, **kwargs):
            message = self._extract_message(args, kwargs)
            if message is not None and await self._should_delete_invocation(message):
                await self._delete_message(message)
            return await original(*args, **kwargs)

        return wrapped

    def _extract_message(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[discord.Message]:
        for value in args:
            if isinstance(value, discord.Message):
                return value
        for value in kwargs.values():
            if isinstance(value, discord.Message):
                return value
        return None

    async def _should_delete_invocation(self, message: discord.Message) -> bool:
        if message.guild is None or message.author.bot:
            return False

        candidates = await self._get_invocation_candidates(message)
        if not candidates:
            return False

        customcom = self._get_customcom_cog()
        if customcom is None:
            return False

        for candidate_name in ("get_command", "get_cc", "get_custom_command", "get_customcom"):
            candidate = getattr(customcom, candidate_name, None)
            if candidate is None:
                continue

            for candidate_content in candidates:
                try:
                    result = candidate(message.guild, candidate_content)
                except TypeError:
                    try:
                        result = candidate(candidate_content)
                    except Exception:
                        result = None
                except Exception:
                    result = None

                if asyncio.iscoroutine(result):
                    with contextlib.suppress(Exception):
                        result = await result

                if result:
                    return True

        config = getattr(customcom, "config", None)
        if config is None:
            return False

        try:
            guild_data = await config.guild(message.guild).all()
        except Exception:
            return False

        for candidate_content in candidates:
            if self._content_matches_config(guild_data, candidate_content.casefold()):
                return True

        return False

    async def _get_invocation_candidates(self, message: discord.Message) -> list[str]:
        content = message.content.lstrip()
        if not content:
            return []

        prefixes = await self._get_prefixes(message)
        for prefix in sorted(prefixes, key=len, reverse=True):
            if content.startswith(prefix):
                remainder = content[len(prefix):].strip()
                if not remainder:
                    return []

                token = remainder.split(maxsplit=1)[0]
                candidates = [token]
                if remainder != token:
                    candidates.append(remainder)
                return candidates

        return []

    async def _get_prefixes(self, message: discord.Message) -> list[str]:
        prefixes = self.bot.get_prefix(message)
        if asyncio.iscoroutine(prefixes):
            prefixes = await prefixes

        if isinstance(prefixes, str):
            return [prefixes]

        return [prefix for prefix in prefixes if isinstance(prefix, str)]

    def _content_matches_config(self, value: Any, normalized_content: str) -> bool:
        if isinstance(value, dict):
            if self._mapping_looks_like_command_table(value):
                for key in value:
                    if isinstance(key, str) and key.strip().casefold() == normalized_content:
                        return True

            for nested in value.values():
                if self._content_matches_config(nested, normalized_content):
                    return True

        elif isinstance(value, list):
            for item in value:
                if self._content_matches_config(item, normalized_content):
                    return True

        return False

    def _mapping_looks_like_command_table(self, mapping: dict[Any, Any]) -> bool:
        response_keys = {
            "content",
            "response",
            "text",
            "message",
            "reply",
            "embed",
            "embeds",
            "tts",
            "delete_trigger",
            "allow_deletion",
        }
        for item in mapping.values():
            if isinstance(item, dict) and any(key in item for key in response_keys):
                return True
            if isinstance(item, (str, list, tuple)):
                return True
        return False

    async def _delete_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return

        me = message.guild.me
        if me is None:
            return

        permissions = message.channel.permissions_for(me)
        if not permissions.manage_messages:
            return

        with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
            await message.delete()

    async def _get_previous_human_message(self, message: discord.Message) -> Optional[discord.Message]:
        with contextlib.suppress(discord.HTTPException, discord.Forbidden):
            async for candidate in message.channel.history(limit=5, before=message):
                if candidate.author.bot:
                    continue
                if candidate.guild is None:
                    continue
                return candidate
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.author.bot or message.guild is None:
            return

        previous = await self._get_previous_human_message(message)
        if previous is None:
            return

        if await self._should_delete_invocation(previous):
            await self._delete_message(previous)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClearCCInvoc(bot))
