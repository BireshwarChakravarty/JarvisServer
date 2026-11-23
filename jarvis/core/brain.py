"""The brain: a streaming Claude agent loop tuned for voice.

* Streams the reply and hands complete sentences to the voice as soon as they
  form, so JARVIS starts talking within a second or so instead of waiting.
* Runs local tools (Mac control, camera, memory …) in parallel, plus Anthropic's
  server-side web search.
* Keeps each conversation append-only (frozen system prompt + tool set per
  session) so the prompt cache stays warm and thinking blocks stay valid. Long
  or stale conversations roll over into a fresh session seeded with a summary.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.aio import spawn
from jarvis.core.persona import build_system_prompt, context_line
from jarvis.events import bus
from jarvis.voice.text import SentenceSplitter, clean_for_speech

log = logging.getLogger("jarvis.brain")

SentenceCallback = Callable[[str], Awaitable[None]]

FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}


@dataclass
class Session:
    id: str
    system: str
    tools: list[dict]
    messages: list[dict] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    turns: int = 0
    seeded: bool = False


class Brain:
    def __init__(self, cfg, registry, memory, tool_ctx, client: Any = None) -> None:
        self.cfg = cfg
        self.registry = registry
        self.memory = memory
        self.tool_ctx = tool_ctx
        self._client = client
        self.session: Session | None = None
        self._lock = asyncio.Lock()
        self.extra_tools: list[dict] = []  # e.g. MCP tools, registered before first session
        self.extra_handlers: dict[str, Callable[[dict], Awaitable[Any]]] = {}

    # ── client ───────────────────────────────────────────────────────────
    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(max_retries=2, timeout=120.0)
        return self._client

    # ── sessions ─────────────────────────────────────────────────────────
    def _tool_schemas(self) -> list[dict]:
        tools = self.registry.schemas(eager=True) + list(self.extra_tools)
        if self.cfg.get("brain.web_search", True):
            tools.append(dict(WEB_SEARCH_TOOL))
        return tools

    def _new_session(self) -> Session:
        sid = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        system = build_system_prompt(self.cfg.get("owner.name", ""), self.cfg.get("owner.title", "sir"))
        session = Session(id=sid, system=system, tools=self._tool_schemas())
        if self.memory:
            self.memory.start_session(sid)
        bus.emit("session", id=sid, status="started")
        log.info("new session %s", sid)
        return session

    def _session_needs_roll(self) -> bool:
        s = self.session
        if s is None:
            return True
        idle = time.time() - s.last_active
        return idle > 60 * float(self.cfg.get("brain.session_idle_minutes", 15)) or \
            s.turns >= int(self.cfg.get("brain.session_max_turns", 40))

    async def ensure_session(self) -> Session:
        if self._session_needs_roll():
            old = self.session
            self.session = self._new_session()
            if old is not None and old.turns > 0:
                spawn(self._summarise(old))
        return self.session  # type: ignore[return-value]

    def reset(self) -> None:
        old = self.session
        self.session = None
        if old is not None and old.turns > 0:
            spawn(self._summarise(old))

    def _seed_block(self) -> str | None:
        """Long-term memory, injected once at the top of a new conversation."""
        if not self.memory:
            return None
        parts: list[str] = []
        facts = self.memory.all_facts(limit=60)
        if facts:
            parts.append("Things you know about your user (long-term memory):\n" +
                         "\n".join(f"- {f['text']}" for f in facts))
        summaries = self.memory.recent_summaries(limit=4)
        if summaries:
            lines = []
            for s in summaries:
                when = dt.datetime.fromtimestamp(s["started"]).strftime("%a %-d %b %-I:%M %p")
                lines.append(f"- {when}: {s['summary']}")
            parts.append("Recent conversations:\n" + "\n".join(lines))
        if not parts:
            return None
        return "[context] " + "\n\n".join(parts)

    # ── public API ───────────────────────────────────────────────────────
    async def respond(
        self,
        text: str | None,
        *,
        on_sentence: SentenceCallback | None = None,
        context: dict | None = None,
        event_note: str | None = None,
        images: list[dict] | None = None,
        cancel: asyncio.Event | None = None,
    ) -> str:
        """Run one user turn through Claude. Returns the full spoken reply.

        text        what the user said (None for system-initiated turns like greetings)
        event_note  telemetry describing why JARVIS is speaking unprompted
        images      optional Claude image blocks to include with the turn
        """
        async with self._lock:
            if self.cfg.get("brain.provider", "anthropic") == "ollama":
                return await self._respond_ollama(text or event_note or "", on_sentence, cancel)
            session = await self.ensure_session()
            content: list[dict] = []
            if not session.seeded:
                seed = self._seed_block()
                if seed:
                    content.append({"type": "text", "text": seed})
                session.seeded = True
            tz = self.cfg.get("owner.timezone") or time.strftime("%Z")
            content.append({"type": "text", "text": context_line(dt.datetime.now().astimezone(), tz, context)})
            if event_note:
                content.append({"type": "text", "text": f"[context] event: {event_note}"})
            if images:
                content.extend(images)
            if text:
                content.append({"type": "text", "text": text})
                if self.memory:
                    self.memory.log_turn(session.id, "user", text)
            session.messages.append({"role": "user", "content": content})
            session.turns += 1
            session.last_active = time.time()
            try:
                reply = await self._run_loop(session, on_sentence, cancel)
            except Exception as exc:  # network/auth etc. — always say *something*
                reply = await self._handle_failure(exc, text or event_note or "", on_sentence, cancel)
            session.last_active = time.time()
            if self.memory and reply:
                self.memory.log_turn(session.id, "assistant", reply)
            return reply

    # ── the agent loop ───────────────────────────────────────────────────
    def _request_params(self, session: Session) -> dict:
        from jarvis.core import speed

        b = self.cfg.get
        model, effort = speed.api(self.cfg)
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": int(b("brain.max_tokens", 8000)),
            "system": [{"type": "text", "text": session.system, "cache_control": {"type": "ephemeral"}}],
            "tools": session.tools,
            "messages": session.messages,
            "output_config": {"effort": effort},
            "cache_control": {"type": "ephemeral"},  # auto-cache the growing conversation
        }
        betas: list[str] = []
        if b("brain.server_fallbacks", True):
            betas.append(FALLBACK_BETA)
            params["fallbacks"] = "default"
        if b("brain.fast_mode", False):
            params["speed"] = "fast"
        if betas:
            params["betas"] = betas
        return params

    async def _run_loop(self, session: Session, on_sentence: SentenceCallback | None,
                        cancel: asyncio.Event | None) -> str:
        spoken: list[str] = []
        json_retries = 0
        for _ in range(12):  # hard cap on tool round-trips per turn
            splitter = SentenceSplitter()
            bus.emit("state", state="thinking")

            async def speak(sentences: list[str]) -> None:
                for s in sentences:
                    s = clean_for_speech(s)
                    if s:
                        spoken.append(s)
                        bus.emit("reply", text=s)
                        if on_sentence:
                            await on_sentence(s)

            try:
                async with self.client.beta.messages.stream(**self._request_params(session)) as stream:
                    async for event in stream:
                        if cancel is not None and cancel.is_set():
                            break
                        etype = getattr(event, "type", "")
                        if etype == "text":
                            await speak(splitter.feed(event.text))
                        elif etype == "content_block_start":
                            block = event.content_block
                            if getattr(block, "type", "") == "server_tool_use":
                                bus.emit("tool", name=getattr(block, "name", "web_search"), status="start",
                                         category="web")
                            elif getattr(block, "type", "") == "tool_use":
                                bus.emit("tool", name=block.name, status="planning")
                    if cancel is not None and cancel.is_set():
                        log.info("turn cancelled mid-stream")
                        bus.emit("interrupted")
                        return " ".join(spoken)
                    final = await stream.get_final_message()
                json_retries = 0
            except ValueError:
                # Eager tool-input streaming produced JSON the SDK couldn't parse at all.
                json_retries += 1
                if json_retries > 2:
                    raise
                continue

            await speak(splitter.flush())
            self._log_usage(final)

            if final.stop_reason == "pause_turn":
                session.messages.append({"role": "assistant", "content": final.content})
                continue

            tool_uses = [b for b in final.content if getattr(b, "type", "") == "tool_use"]
            if final.stop_reason == "refusal":
                session.messages.append({"role": "assistant", "content": final.content})
                if not spoken:
                    await speak(["I'm afraid that's one request I can't help with."])
                break
            if not tool_uses:
                session.messages.append({"role": "assistant", "content": final.content})
                break
            if final.stop_reason == "max_tokens":
                # a truncated tool input parses as a valid partial object — never run it
                session.messages.append({"role": "assistant", "content": final.content})
                results = [{"type": "tool_result", "tool_use_id": t.id, "is_error": True,
                            "content": "Tool input was truncated (max_tokens). Try a shorter call."}
                           for t in tool_uses]
                session.messages.append({"role": "user", "content": results})
                continue

            session.messages.append({"role": "assistant", "content": final.content})
            bus.emit("state", state="working")
            results = await asyncio.gather(*(self._execute(t) for t in tool_uses))
            session.messages.append({"role": "user", "content": list(results)})
            if cancel is not None and cancel.is_set():
                bus.emit("interrupted")
                return " ".join(spoken)
        return " ".join(spoken)

    async def _execute(self, block: Any) -> dict:
        name, args = block.name, block.input
        if name in self.extra_handlers:
            from jarvis.tools.registry import ToolResult, _normalise

            bus.emit("tool", name=name, status="start", args=args, category="mcp")
            try:
                value = await self.extra_handlers[name](args if isinstance(args, dict) else {})
                result = ToolResult(_normalise(value))
            except Exception as exc:
                result = ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
            bus.emit("tool", name=name, status="error" if result.is_error else "done", summary=result.summary)
            return result.to_block(block.id)
        result = await self.registry.call(name, args, self.tool_ctx)
        return result.to_block(block.id)

    def _log_usage(self, msg: Any) -> None:
        u = getattr(msg, "usage", None)
        if not u:
            return
        bus.emit(
            "usage",
            model=getattr(msg, "model", ""),
            input=getattr(u, "input_tokens", 0) or 0,
            output=getattr(u, "output_tokens", 0) or 0,
            cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )

    # ── failure handling & offline mode ──────────────────────────────────
    async def _handle_failure(self, exc: Exception, prompt: str, on_sentence: SentenceCallback | None,
                              cancel: asyncio.Event | None) -> str:
        import anthropic

        log.exception("brain failure")
        # Drop the dangling user turn so the next request stays well-formed
        if self.session and self.session.messages and self.session.messages[-1]["role"] == "user":
            # Only safe when it's the turn we just appended (no assistant reply yet)
            last = self.session.messages[-1]["content"]
            if not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in last):
                self.session.messages.pop()
                self.session.turns -= 1
            else:  # mid tool loop — start clean next time
                self.session = None
        if isinstance(exc, anthropic.APIConnectionError) and await self._ollama_available():
            return await self._respond_ollama(prompt, on_sentence, cancel)
        if isinstance(exc, anthropic.AuthenticationError) or "authentication method" in str(exc):
            msg = "My Anthropic API key appears to be missing or invalid. Run jarvis doctor and I'll walk you through it."
        elif isinstance(exc, anthropic.RateLimitError):
            msg = "I'm being rate limited at the moment. Give me a few seconds and try again."
        elif isinstance(exc, anthropic.APIConnectionError):
            msg = "I can't reach my servers. The network appears to be down."
        elif isinstance(exc, anthropic.APIStatusError):
            msg = f"My servers returned an error, status {exc.status_code}. Please try again."
        else:
            msg = "Something went wrong on my end. I've logged the details."
        bus.emit("error", message=str(exc)[:400])
        bus.emit("reply", text=msg)
        if on_sentence:
            await on_sentence(msg)
        return msg

    async def _ollama_available(self) -> bool:
        import httpx

        host = self.cfg.get("brain.ollama.host", "http://localhost:11434")
        try:
            async with httpx.AsyncClient(timeout=1.5) as http:
                r = await http.get(f"{host}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def _respond_ollama(self, prompt: str, on_sentence: SentenceCallback | None,
                              cancel: asyncio.Event | None) -> str:
        """Offline mode: local model via Ollama (chat only, no tools)."""
        import httpx

        host = self.cfg.get("brain.ollama.host", "http://localhost:11434")
        model = self.cfg.get("brain.ollama.model", "llama3.2")
        system = build_system_prompt(self.cfg.get("owner.name", ""), self.cfg.get("owner.title", "sir"))
        system += "\nYou are currently in offline mode: you cannot use tools, so answer from knowledge only."
        splitter, spoken = SentenceSplitter(), []
        bus.emit("state", state="thinking")
        async with httpx.AsyncClient(timeout=120) as http, http.stream("POST", f"{host}/api/chat", json={
            "model": model, "stream": True,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }) as r:
            async for line in r.aiter_lines():
                if cancel is not None and cancel.is_set():
                    break
                if not line.strip():
                    continue
                chunk = json.loads(line)
                for s in splitter.feed(chunk.get("message", {}).get("content", "")):
                    s = clean_for_speech(s)
                    spoken.append(s)
                    bus.emit("reply", text=s)
                    if on_sentence:
                        await on_sentence(s)
        for s in splitter.flush():
            s = clean_for_speech(s)
            spoken.append(s)
            bus.emit("reply", text=s)
            if on_sentence:
                await on_sentence(s)
        return " ".join(spoken)

    # ── summaries ────────────────────────────────────────────────────────
    async def _summarise(self, session: Session) -> None:
        if not self.memory:
            return
        transcript = self.memory.session_transcript(session.id)
        if not transcript:
            self.memory.end_session(session.id, None)
            return
        text = "\n".join(f"{t['role']}: {t['text']}" for t in transcript)[-12000:]
        try:
            msg = await self.client.messages.create(
                model=self.cfg.get("brain.model", "claude-opus-5-5"),
                max_tokens=1024,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": (
                    "Summarise this conversation between a user and their assistant JARVIS in one or two "
                    "sentences for JARVIS's memory: what was asked, what was done, anything left pending. "
                    "Plain text only.\n\n" + text)}],
            )
            summary = " ".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        except Exception as exc:
            log.warning("summary failed: %s", exc)
            summary = transcript[0]["text"][:200]
        self.memory.end_session(session.id, summary)
        bus.emit("session", id=session.id, status="summarised", summary=summary)
