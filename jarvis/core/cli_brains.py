"""Brains that run on your *monthly plan* instead of a pay-per-use API key.

ClaudeCodeBrain  drives the official `claude` CLI (Claude Code) signed in with your Claude
                 Pro/Max account. One CLI process stays warm for the whole conversation
                 (stream-json in, stream-json out), so replies start streaming within a second.
CodexBrain       drives the official `codex` CLI signed in with your ChatGPT plan
                 (`codex exec --json`, resuming the same thread every turn).

Policy notes (see RESEARCH.md): JARVIS never reads, stores or relays your Claude/ChatGPT
credentials — the official, unmodified CLIs handle sign-in themselves. JARVIS's own tools
reach the CLI through a local MCP endpoint (jarvis/tools/mcp_server.py).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from jarvis.aio import spawn
from jarvis.core.persona import build_system_prompt, context_line
from jarvis.events import bus
from jarvis.voice.text import SentenceSplitter, clean_for_speech

log = logging.getLogger("jarvis.cli_brain")

SentenceCallback = Callable[[str], Awaitable[None]]

CLAUDE_PATHS = ["~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
CODEX_PATHS = ["~/.local/bin/codex", "~/.codex/bin/codex", "/opt/homebrew/bin/codex", "/usr/local/bin/codex"]
# Credentials that would silently switch the CLIs to pay-per-use API billing.
API_BILLING_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY")


def find_binary(name: str, configured: str | None = None) -> str | None:
    if configured:
        p = Path(configured).expanduser()
        return str(p) if p.exists() else None
    for cand in (CLAUDE_PATHS if name == "claude" else CODEX_PATHS):
        p = Path(cand).expanduser()
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which(name)


def child_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in API_BILLING_ENV}
    paths = [str(Path("~/.local/bin").expanduser()), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    env["PATH"] = ":".join(paths + [env.get("PATH", "")])
    env.setdefault("DISABLE_AUTOUPDATER", "1")
    env.update({str(k): str(v) for k, v in (extra or {}).items()})  # tests / advanced config only
    return env


async def _speak_all(text: str, splitter: SentenceSplitter, spoken: list[str], on_sentence: SentenceCallback | None,
                     final: bool = False) -> None:
    sentences = splitter.feed(text) if text else []
    if final:
        sentences += splitter.flush()
    for s in sentences:
        s = clean_for_speech(s)
        if s:
            spoken.append(s)
            bus.emit("reply", text=s)
            if on_sentence:
                await on_sentence(s)


class _CLIBrain:
    provider = "cli"

    def __init__(self, cfg, registry, memory, tool_ctx) -> None:
        self.cfg = cfg
        self.registry = registry
        self.memory = memory
        self.tool_ctx = tool_ctx
        self.extra_tools: list[dict] = []
        self.extra_handlers: dict[str, Any] = {}
        self.mcp = None
        self.session = None  # compatibility with Brain
        self._lock = asyncio.Lock()
        self.session_id: str | None = None
        self.turns = 0
        self.last_active = 0.0
        self.seeded = False
        self.workspace = cfg.home / "brain-workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    async def _ensure_mcp(self) -> str:
        if self.mcp is None:
            from jarvis.tools.mcp_server import MCPServer

            self.mcp = MCPServer(self.registry, self.tool_ctx, self.extra_tools, self.extra_handlers)
            await self.mcp.start()
        return self.mcp.url

    def system_prompt(self) -> str:
        return build_system_prompt(self.cfg.get("owner.name", ""), self.cfg.get("owner.title", "sir")) + (
            "\nYour abilities are the tools of the `jarvis` MCP server (Mac control, calendar, camera, memory, timers, "
            "protocols, music, messages) plus web search. Use them freely; never mention tool names out loud.")

    def _needs_new_session(self) -> bool:
        idle = time.time() - self.last_active if self.last_active else 0
        return (self.session_id is None or idle > 60 * float(self.cfg.get("brain.session_idle_minutes", 15))
                or self.turns >= int(self.cfg.get("brain.session_max_turns", 40)))

    def _seed(self) -> str:
        if not self.memory:
            return ""
        parts = []
        facts = self.memory.all_facts(limit=60)
        if facts:
            parts.append("Things you know about your user:\n" + "\n".join(f"- {f['text']}" for f in facts))
        summaries = self.memory.recent_summaries(limit=4)
        if summaries:
            parts.append("Recent conversations:\n" + "\n".join(f"- {s['summary']}" for s in summaries))
        return ("[context] " + "\n\n".join(parts)) if parts else ""

    def _compose(self, text: str | None, context: dict | None, event_note: str | None, first: bool) -> str:
        tz = self.cfg.get("owner.timezone") or time.strftime("%Z")
        bits = []
        if first:
            seed = self._seed()
            if seed:
                bits.append(seed)
        bits.append(context_line(dt.datetime.now().astimezone(), tz, context))
        if event_note:
            bits.append(f"[context] event: {event_note}")
        if text:
            bits.append(text)
        return "\n\n".join(bits)

    def reset(self) -> None:
        self.session_id = None

    async def _speak_error(self, msg: str, on_sentence: SentenceCallback | None) -> str:
        bus.emit("reply", text=msg)
        if on_sentence:
            await on_sentence(msg)
        return msg


class ClaudeCodeBrain(_CLIBrain):
    provider = "claude-code"

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.proc: asyncio.subprocess.Process | None = None
        self._exited_silently = False
        self._stderr_task: asyncio.Task | None = None
        self.binary = None

    def _args(self, mcp_url: str, resume: bool) -> list[str]:
        mcp = {"mcpServers": {"jarvis": {"type": "http", "url": mcp_url}}}
        args = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--system-prompt", self.system_prompt(),
                "--mcp-config", json.dumps(mcp), "--strict-mcp-config",
                "--tools", "WebSearch", "WebFetch",
                "--allowedTools", "mcp__jarvis", "WebSearch", "WebFetch",
                "--permission-mode", "dontAsk"]
        args += ["--resume", self.session_id] if resume else ["--session-id", self.session_id]
        model = self.cfg.get("brain.claude_model")
        if model:
            args += ["--model", str(model)]
        return args

    async def _start(self, resume: bool) -> None:
        self.binary = find_binary("claude", self.cfg.get("brain.claude_path"))
        if not self.binary:
            raise FileNotFoundError("Claude Code isn't installed. Open Settings › Intelligence to set it up.")
        url = await self._ensure_mcp()
        await self._stop()
        self.proc = await asyncio.create_subprocess_exec(
            self.binary, *self._args(url, resume), cwd=str(self.workspace),
            env=child_env(self.cfg.get("brain.cli_env")),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=32 * 1024 * 1024)
        self._stderr_task = asyncio.ensure_future(self._pump_stderr(self.proc))
        log.info("claude CLI started (session %s, resume=%s)", self.session_id, resume)

    async def _pump_stderr(self, proc) -> None:
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            log.debug("claude: %s", line.decode(errors="replace").rstrip())

    async def _stop(self) -> None:
        p, self.proc = self.proc, None
        if p is not None and p.returncode is None:
            try:
                p.stdin.close()
                await asyncio.wait_for(p.wait(), 3)
            except Exception:
                p.kill()

    def reset(self) -> None:
        super().reset()
        spawn(self._stop())

    async def respond(self, text: str | None, *, on_sentence: SentenceCallback | None = None,
                      context: dict | None = None, event_note: str | None = None,
                      images: list[dict] | None = None, cancel: asyncio.Event | None = None) -> str:
        async with self._lock:
            first = False
            try:
                if self._needs_new_session():
                    self.session_id = str(uuid.uuid4())
                    self.turns, self.seeded = 0, False
                    await self._start(resume=False)
                    first = True
                    if self.memory:
                        self.memory.start_session(self.session_id)
                elif self.proc is None or self.proc.returncode is not None:
                    await self._start(resume=True)
            except FileNotFoundError as exc:
                return await self._speak_error(str(exc), on_sentence)
            prompt = self._compose(text, context, event_note, first and not self.seeded)
            self.seeded = True
            msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}}
            if text and self.memory:
                self.memory.log_turn(self.session_id, "user", text)
            self.turns += 1
            self.last_active = time.time()
            bus.emit("state", state="thinking")
            limit = float(self.cfg.get("brain.turn_timeout_seconds", 240))
            for attempt in (1, 2):
                self._exited_silently = False
                try:
                    self.proc.stdin.write((json.dumps(msg) + "\n").encode())
                    await self.proc.stdin.drain()
                    reply = await asyncio.wait_for(self._read_turn(on_sentence, cancel, quiet_exit=attempt == 1), limit)
                except asyncio.TimeoutError:
                    # a stalled turn must not block every request after it
                    log.error("claude turn exceeded %.0fs; stopping it", limit)
                    await self._stop()
                    reply = await self._speak_error("That was taking far too long, so I've stopped it. "
                                                    "Shall I try again?", on_sentence)
                except (BrokenPipeError, ConnectionResetError) as exc:
                    log.error("claude CLI pipe broke: %s", exc)
                    self.proc = None
                    reply = await self._speak_error("I lost my connection to Claude for a moment. Please say that again.",
                                                    on_sentence)
                if not (self._exited_silently and attempt == 1):
                    break
                # The CLI quit without a word (typically: the saved session can't be resumed).
                # Start a fresh session and send the same request once more.
                log.warning("claude CLI exited silently; retrying in a fresh session")
                self.session_id = str(uuid.uuid4())
                self.turns, self.seeded = 1, True
                await self._start(resume=False)
                if self.memory:
                    self.memory.start_session(self.session_id)
                msg = {"type": "user", "message": {"role": "user", "content": [
                    {"type": "text", "text": self._compose(text, context, event_note, True)}]}}
            self.last_active = time.time()
            if self.memory and reply:
                self.memory.log_turn(self.session_id, "assistant", reply)
            return reply

    async def _read_turn(self, on_sentence: SentenceCallback | None, cancel: asyncio.Event | None,
                         quiet_exit: bool = False) -> str:
        splitter, spoken = SentenceSplitter(), []
        streamed = False
        interrupted = False
        assert self.proc is not None
        while True:
            if cancel is not None and cancel.is_set() and not interrupted:
                interrupted = True
                await self._interrupt()
            try:
                raw = await asyncio.wait_for(self.proc.stdout.readline(), timeout=0.25 if not interrupted else 8)
            except asyncio.TimeoutError:
                if interrupted:
                    await self._stop()  # didn't acknowledge the interrupt: restart next turn (resumes)
                    return " ".join(spoken)
                continue
            if not raw:  # process exited
                code = await self.proc.wait()
                log.error("claude CLI exited (%s)", code)
                self.proc = None
                if not spoken:
                    if quiet_exit:
                        self._exited_silently = True   # the caller retries once in a fresh session
                        return ""
                    return await self._speak_error(
                        "Claude Code stopped unexpectedly. If you've just installed it, open Settings and sign in "
                        "with your Claude plan.", on_sentence)
                return " ".join(spoken)
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            et = ev.get("type")
            if et == "stream_event" and not interrupted:
                inner = ev.get("event") or {}
                it = inner.get("type")
                if it == "content_block_delta" and (inner.get("delta") or {}).get("type") == "text_delta":
                    streamed = True
                    await _speak_all(inner["delta"].get("text", ""), splitter, spoken, on_sentence)
                elif it == "content_block_start":
                    block = inner.get("content_block") or {}
                    if block.get("type") in ("tool_use", "server_tool_use"):
                        await _speak_all("", splitter, spoken, on_sentence, final=True)
                        name = block.get("name", "")
                        if not name.startswith("mcp__jarvis__"):
                            bus.emit("tool", name="web_search" if "Search" in name else name, status="start",
                                     category="web")
                        bus.emit("state", state="working")
                elif it == "message_stop":
                    await _speak_all("", splitter, spoken, on_sentence, final=True)
            elif et == "assistant" and not streamed and not interrupted:
                for block in (ev.get("message") or {}).get("content", []):
                    if block.get("type") == "text":
                        await _speak_all(block.get("text", ""), splitter, spoken, on_sentence, final=True)
            elif et == "user":
                # tool results came back; web-search finished
                bus.emit("state", state="thinking")
            elif et == "system" and ev.get("subtype") == "init":
                servers = {s.get("name"): s.get("status") for s in ev.get("mcp_servers", [])}
                if servers.get("jarvis") not in (None, "connected"):
                    log.warning("JARVIS tools not connected in Claude Code: %s", servers)
            elif et == "result":
                if not interrupted:
                    await _speak_all("", splitter, spoken, on_sentence, final=True)
                if ev.get("is_error") and not spoken and not interrupted:
                    return await self._speak_error(_friendly_error(str(ev.get("result") or ev.get("subtype"))),
                                                   on_sentence)
                if not spoken and ev.get("result") and not interrupted:
                    await _speak_all(str(ev["result"]), splitter, spoken, on_sentence, final=True)
                usage = ev.get("usage") or {}
                bus.emit("usage", model="claude-code", input=usage.get("input_tokens", 0),
                         output=usage.get("output_tokens", 0), cache_read=usage.get("cache_read_input_tokens", 0))
                return " ".join(spoken)

    async def _interrupt(self) -> None:
        try:
            req = {"type": "control_request", "request_id": uuid.uuid4().hex,
                   "request": {"subtype": "interrupt"}}
            self.proc.stdin.write((json.dumps(req) + "\n").encode())
            await self.proc.stdin.drain()
            bus.emit("interrupted")
        except Exception:
            await self._stop()


def _friendly_error(text: str) -> str:
    t = text.lower()
    if "limit" in t and ("usage" in t or "reset" in t or "rate" in t):
        return "We've reached your plan's usage limit for now. It resets shortly — I'll be ready when it does."
    if "login" in t or "auth" in t or "unauthorized" in t or "401" in t:
        return "I'm not signed in to your plan. Open Settings, then Intelligence, and sign in again."
    if "overloaded" in t or "529" in t:
        return "Claude is overloaded at the moment. Give it a few seconds and ask again."
    return "Something went wrong on Claude's side. Please try that again."


class CodexBrain(_CLIBrain):
    """ChatGPT plan via the official Codex CLI (`codex exec --json`). Replies arrive per message,
    so speech starts a little later than with Claude Code's token streaming."""
    provider = "codex"

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.thread_id: str | None = None

    def reset(self) -> None:
        super().reset()
        self.thread_id = None

    def _args(self, mcp_url: str, prompt: str) -> list[str]:
        args = ["exec"]
        if self.thread_id:
            args += ["resume", self.thread_id]
        args += ["--json", "--skip-git-repo-check",
                 "-c", f'mcp_servers.jarvis.url="{mcp_url}"',
                 "-c", 'mcp_servers.jarvis.default_tools_approval_mode="approve"',
                 "-c", "mcp_servers.jarvis.tool_timeout_sec=180"]
        if not self.thread_id:
            args += ["--sandbox", "read-only"]
        model = self.cfg.get("brain.codex_model")
        if model:
            args += ["-m", str(model)]
        return args + [prompt]

    async def respond(self, text: str | None, *, on_sentence: SentenceCallback | None = None,
                      context: dict | None = None, event_note: str | None = None,
                      images: list[dict] | None = None, cancel: asyncio.Event | None = None) -> str:
        async with self._lock:
            binary = find_binary("codex", self.cfg.get("brain.codex_path"))
            if not binary:
                return await self._speak_error("Codex isn't installed. Open Settings, then Intelligence, to set it up.",
                                               on_sentence)
            first = self._needs_new_session()
            if first:
                self.session_id, self.thread_id, self.turns = str(uuid.uuid4()), None, 0
            prompt = self._compose(text, context, event_note, first)
            if first:
                prompt = self.system_prompt() + "\n\n---\n\n" + prompt
            self.turns += 1
            self.last_active = time.time()
            url = await self._ensure_mcp()
            bus.emit("state", state="thinking")
            proc = await asyncio.create_subprocess_exec(
                binary, *self._args(url, prompt), cwd=str(self.workspace), env=child_env(self.cfg.get("brain.cli_env")),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=32 * 1024 * 1024)
            splitter, spoken, err = SentenceSplitter(), [], ""
            while True:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    break
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                if not raw:
                    break
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                et = ev.get("type", "")
                item = ev.get("item") or {}
                if et == "thread.started":
                    self.thread_id = ev.get("thread_id") or self.thread_id
                elif et == "item.started" and item.get("type") in ("mcp_tool_call", "web_search"):
                    bus.emit("state", state="working")
                    if item.get("type") == "web_search":
                        bus.emit("tool", name="web_search", status="start", category="web")
                elif et == "item.completed" and item.get("type") == "agent_message":
                    await _speak_all(item.get("text", ""), splitter, spoken, on_sentence, final=True)
                elif et in ("turn.failed", "error"):
                    err = str((ev.get("error") or {}).get("message") or ev.get("message") or "")
            await proc.wait()
            if not spoken:
                return await self._speak_error(_friendly_error(err) if err else
                                               "Codex didn't answer. Check you're signed in under Settings.", on_sentence)
            return " ".join(spoken)


def make_brain(cfg, registry, memory, tool_ctx):
    provider = cfg.get("brain.provider", "anthropic")
    if provider == "claude-code":
        return ClaudeCodeBrain(cfg, registry, memory, tool_ctx)
    if provider == "codex":
        return CodexBrain(cfg, registry, memory, tool_ctx)
    from jarvis.core.brain import Brain

    return Brain(cfg, registry, memory, tool_ctx)
