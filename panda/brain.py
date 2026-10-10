"""The conversation engine: streams Claude's reply and runs tools in a loop.

History is append-only. Assistant turns are stored exactly as the API returned
them (thinking blocks included), and per-turn context is appended as a
mid-conversation system message rather than edited into the system prompt, so
the prompt cache and preserved thinking both stay valid.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Any, Awaitable, Callable

import anthropic

from . import db, persona, tools
from .config import settings

log = logging.getLogger("panda.brain")
Emit = Callable[[dict], Awaitable[None]]

MAX_TOOL_ROUNDS = 12
MAX_MESSAGES = 400  # start a fresh conversation beyond this; memories carry over

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)


def _block_to_param(block: Any) -> dict:
    data = block.to_dict(mode="json") if hasattr(block, "to_dict") else dict(block)
    return {k: v for k, v in data.items() if v is not None}


def _with_cache_breakpoint(messages: list[dict]) -> list[dict]:
    """Copy of messages with a cache breakpoint on the last user turn."""
    out = copy.deepcopy(messages)
    for msg in reversed(out):
        if msg["role"] != "user":
            continue
        content = msg["content"]
        if isinstance(content, str):
            msg["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
        elif content:
            content[-1]["cache_control"] = {"type": "ephemeral"}
        break
    return out


class Conversation:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        row = db.latest_conversation()
        if row is None or len(row["messages"]) >= MAX_MESSAGES:
            self.id = db.new_conversation()
            self.messages: list[dict] = []
            self.transcript: list[dict] = []
        else:
            self.id = row["id"]
            self.messages = row["messages"]
            self.transcript = row["transcript"]

    def reset(self) -> None:
        self.id = db.new_conversation()
        self.messages = []
        self.transcript = []

    def save(self) -> None:
        title = next((t["text"][:80] for t in self.transcript if t["role"] == "user"), "")
        db.save_conversation(self.id, self.messages, self.transcript, title)

    async def _request(self, emit: Emit):
        kwargs: dict[str, Any] = dict(
            model=settings.model,
            max_tokens=16000,
            system=[{"type": "text", "text": persona.system_prompt(), "cache_control": {"type": "ephemeral"}}],
            tools=tools.all_tools(),
            messages=_with_cache_breakpoint(self.messages),
            output_config={"effort": settings.effort},
        )
        if settings.fallbacks != "off":
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = settings.fallbacks

        async with _client.beta.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "text":
                    await emit({"type": "delta", "text": event.text})
                elif event.type == "content_block_start":
                    block = event.content_block
                    if block.type in ("tool_use", "server_tool_use"):
                        await emit({"type": "tool", "name": block.name})
            return await stream.get_final_message()

    async def respond(self, user_text: str, emit: Emit) -> None:
        """Handle one user utterance end to end."""
        async with self.lock:
            if len(self.messages) >= MAX_MESSAGES:
                self.reset()
            turn_start = len(self.messages)
            self.messages.append({"role": "user", "content": user_text})
            self.messages.append({"role": "system", "content": persona.context_note(user_text)})
            self.transcript.append({"role": "user", "text": user_text, "at": db.now_iso()})
            await emit({"type": "start"})

            spoken: list[str] = []
            refreshed = False
            json_retries = 0
            rounds = 0
            try:
                while rounds < MAX_TOOL_ROUNDS:
                    try:
                        response = await self._request(emit)
                    except ValueError:
                        # Tool input JSON the SDK could not parse at all; re-issue the turn.
                        json_retries += 1
                        if json_retries > 2:
                            raise
                        continue
                    json_retries = 0
                    rounds += 1

                    if response.stop_reason == "refusal":
                        text = f"I'm afraid that's one request I can't help with, {settings.owner_title}."
                        self.messages.append({"role": "assistant", "content": text})
                        await emit({"type": "delta", "text": text})
                        spoken.append(text)
                        break

                    content = [_block_to_param(b) for b in response.content]
                    self.messages.append({"role": "assistant", "content": content})
                    spoken += [b.text for b in response.content if b.type == "text"]

                    if response.stop_reason == "pause_turn":
                        continue

                    tool_uses = [b for b in response.content if b.type == "tool_use"]
                    if not tool_uses:
                        break

                    results = []
                    for block in tool_uses:
                        if response.stop_reason == "max_tokens":
                            error = "tool input was truncated; try again with a shorter input"
                        else:
                            error = tools.validate(block.name, block.input)
                        if error:
                            results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                            "content": json.dumps({"INVALID_JSON": error})})
                            continue
                        try:
                            output = await tools.run(block.name, block.input)
                            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
                            if block.name in tools.MUTATING:
                                refreshed = True
                        except Exception as exc:  # tool failures go back to the model, not the user
                            log.exception("tool %s failed", block.name)
                            results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                            "content": f"{type(exc).__name__}: {exc}"})
                    self.messages.append({"role": "user", "content": results})
                    await emit({"type": "delta", "text": " "})
                else:
                    text = "I've gone round in circles on that one. Shall we try a different approach?"
                    self.messages.append({"role": "user", "content": "(stopped: too many tool rounds)"})
                    self.messages.append({"role": "assistant", "content": text})
                    await emit({"type": "delta", "text": text})
                    spoken.append(text)
            except anthropic.AuthenticationError:
                await self._fail(emit, turn_start, "My API key has been rejected. Please check ANTHROPIC_API_KEY.")
                return
            except anthropic.RateLimitError:
                await self._fail(emit, turn_start, "I'm being rate limited by the model provider. Give me a moment and try again.")
                return
            except anthropic.APIConnectionError:
                await self._fail(emit, turn_start, "I can't reach the model servers at the moment. Network trouble, I suspect.")
                return
            except anthropic.APIStatusError as exc:
                log.error("API error %s: %s", exc.status_code, exc.message)
                await self._fail(emit, turn_start, f"The model service returned an error ({exc.status_code}).")
                return
            except Exception:
                log.exception("turn failed")
                await self._fail(emit, turn_start, "Something went wrong on my end. The details are in the server log.")
                return

            reply = " ".join(s.strip() for s in spoken if s.strip())
            self.transcript.append({"role": "assistant", "text": reply, "at": db.now_iso()})
            self.save()
            await emit({"type": "done", "text": reply, "refresh": refreshed})

    async def _fail(self, emit: Emit, turn_start: int, text: str) -> None:
        # Roll back this unfinished turn so the history sent next time stays valid.
        del self.messages[turn_start:]
        self.transcript.append({"role": "assistant", "text": text, "at": db.now_iso(), "error": True})
        self.save()
        await emit({"type": "error", "text": text})
