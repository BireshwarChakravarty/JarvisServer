"""JARVIS's natural conversation: Google's Gemini Live, speech to speech.

Gemini hears you and answers in its own voice, so there is no transcribe-then-synthesise step: the
intonation, the pauses, the warmth and the reading of your tone are the model's own, and it hears you
interrupt it. This is how the best open-source JARVIS projects sound human (Mark-LVII, Jarvis-Live).

  · The wake word stays on this Mac. Only what you say after "Hey Jarvis" is streamed to Google, and
    only while the conversation lasts.
  · His voice plays through the echo canceller, so he never hears himself.
  · Every JARVIS tool is offered to Gemini. Anything that needs real thought or several steps goes to
    JARVIS's own brain (Claude Code on your plan) in the background, while Gemini keeps talking.
  · Risky actions are never done on the first ask: the tool answers "needs approval", Gemini asks you,
    and only your yes (or the HUD's Yes) carries it out.
  · Offline, without a key or out of free quota, the conversation runs on the on-device pipeline
    instead, and Gemini is retried a little later.

Free on Google's free tier (Gemini 3.8 Live). On that tier Google may use what's said to improve its
products, which Settings says plainly where the key is entered.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

import numpy as np

from jarvis.events import bus

log = logging.getLogger("jarvis.live")

MODELS = ["gemini-3.8-live", "gemini-3.1-flash-live-preview"]   # newest first; the next is tried on failure
IN_RATE, OUT_RATE = 16000, 24000
KEY_ENV = "GEMINI_API_KEY"
DEFAULT_VOICE = "Charon"
VOICES = {   # Google's voices, the ones chosen by ear for JARVIS (any of Google's 30 can be set in config.yaml)
    "Charon": "Informative", "Orus": "Firm", "Algieba": "Smooth", "Alnilam": "Firm", "Sulafat": "Warm",
    "Algenib": "Gravelly",
}
ACCENTS = {
    "british": "a refined, warm British accent (Received Pronunciation), like a trusted English butler",
    "irish": "a soft, natural Irish accent",
    "american": "a natural American accent",
    "neutral": "",
}
COOLDOWN = 300          # after a failure (offline, quota), use the on-device voice this long before retrying
BRAIN_TOOL = "ask_jarvis_brain"
CONFIRM_TOOL = "confirm_action"


def api_key() -> str | None:
    key = os.environ.get(KEY_ENV) or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    try:
        from jarvis.mac.keychain import get_api_key

        key = get_api_key(service="JARVIS Gemini API Key")
        if key:
            os.environ[KEY_ENV] = key
        return key
    except Exception:
        return None


def installed() -> bool:
    try:
        import google.genai  # noqa: F401
    except Exception:
        return False
    return True


def wanted(cfg) -> bool:
    return bool(cfg.get("voice.live.enabled", True)) and bool(api_key()) and installed()


def instructions(cfg, context: dict | None = None) -> str:
    """JARVIS's persona, adapted for speaking aloud through Gemini, plus how to use his tools."""
    from jarvis.core.persona import SYSTEM_PROMPT

    owner = cfg.get("owner.name", "") or "the owner"
    title = cfg.get("owner.title", "sir")
    try:
        base = SYSTEM_PROMPT.format(owner_name=owner, title=title)
    except (KeyError, IndexError):
        base = SYSTEM_PROMPT
    accent = ACCENTS.get(str(cfg.get("voice.live.accent", "british")), "")
    lines = [base, "", "Speaking"]
    if accent:
        lines.append(f"- Always speak English with {accent}. Keep the same voice and accent throughout.")
    lines += [
        "- You are talking, not writing: answer straight away, naturally, with the rhythm of real speech.",
        "- If they interrupt, stop and listen; pick up from what they said, not from where you were.",
        "",
        "Your tools",
        "- Use JARVIS's tools for anything about their Mac, calendar, mail, messages, home, files or the web. "
        "Never guess figures or facts a tool can give you.",
        f"- For anything that needs several steps, research, writing, or memory of past conversations, call "
        f"{BRAIN_TOOL} with the whole task in their words. Say a few words first (\"On it.\"), keep talking "
        f"with them if they want, and tell them the result when it arrives.",
        f"- Some actions need their approval. When a tool answers NEEDS_APPROVAL, ask them plainly in one "
        f"short question, and only if they agree call {CONFIRM_TOOL} with the token; if they decline, call it "
        f"with approved false.",
    ]
    if context:
        lines += ["", "Right now"] + [f"- {k}: {v}" for k, v in context.items() if v]
    return "\n".join(lines)


def declarations(tools) -> list:
    """JARVIS's tools in Gemini's terms, plus the hand-off to his brain and the approval step."""
    from google.genai import types

    def plain(schema):       # JSON Schema as Gemini takes it (no additionalProperties)
        if isinstance(schema, dict):
            return {k: plain(v) for k, v in schema.items() if k != "additionalProperties"}
        if isinstance(schema, list):
            return [plain(v) for v in schema]
        return schema

    out = []
    for t in tools:
        schema = plain(dict(t.input_schema or {"type": "object", "properties": {}}))
        out.append(types.FunctionDeclaration(name=t.name, description=(t.description or "")[:1000],
                                             parameters_json_schema=schema))
    out.append(types.FunctionDeclaration(
        name=BRAIN_TOOL,
        description=("Hand a task to JARVIS's planning mind (it can use every tool, remember past conversations, "
                     "research and write). Use it for anything needing more than one simple step. Returns what "
                     "to tell the user."),
        parameters_json_schema={"type": "object", "properties": {
            "task": {"type": "string", "description": "The whole request, in the user's words, with any context."}},
            "required": ["task"]},
        behavior=types.Behavior.NON_BLOCKING))
    out.append(types.FunctionDeclaration(
        name=CONFIRM_TOOL,
        description="Carry out (or drop) an action that needed the user's approval, once they have answered.",
        parameters_json_schema={"type": "object", "properties": {
            "token": {"type": "string"}, "approved": {"type": "boolean"}}, "required": ["token", "approved"]}))
    return out


class LiveVoice:
    """One Gemini Live conversation at a time, opened after the wake word."""

    def __init__(self, j) -> None:
        self.j = j
        self.cfg = j.cfg
        self.resume_handle: str | None = None      # keeps the thread across conversations (valid ~2 h)
        self.down_until = 0.0                      # cooling off after a failure
        self.model_index = 0
        self.pending: dict[str, tuple[str, dict, str]] = {}   # approval token -> (tool, args, action)
        self.last_error = ""

    # ── availability ──────────────────────────────────────────────────────────────────────────────────
    def ready(self) -> bool:
        return wanted(self.cfg) and time.monotonic() >= self.down_until

    def _failed(self, why: str) -> None:
        self.last_error = why
        self.down_until = time.monotonic() + COOLDOWN
        log.warning("Gemini Live unavailable (%s): using the on-device voice for a few minutes", why)
        self.j.capabilities["live_voice"] = f"unavailable ({why}); on-device voice for now"

    def _config(self, context: dict | None):
        from google.genai import types

        voice = str(self.cfg.get("voice.live.voice", DEFAULT_VOICE))
        tools = [types.Tool(function_declarations=declarations(self._tools()))]
        aad = types.AutomaticActivityDetection(
            silence_duration_ms=int(float(self.cfg.get("voice.live.silence_ms", 700))),
            prefix_padding_ms=200)
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
            system_instruction=instructions(self.cfg, context),
            tools=tools,
            input_audio_transcription={},
            output_audio_transcription={},
            realtime_input_config=types.RealtimeInputConfig(automatic_activity_detection=aad),
            session_resumption=types.SessionResumptionConfig(handle=self.resume_handle),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=24000, sliding_window=types.SlidingWindow(target_tokens=12000)),
        )

    def _tools(self) -> list:
        from jarvis.tools.registry import registry

        return list(registry.active())

    def _client(self):
        from google import genai

        return genai.Client(api_key=api_key())

    # ── a conversation ────────────────────────────────────────────────────────────────────────────────
    async def converse(self, q: asyncio.Queue, preroll: list | None = None) -> bool:
        """Talk until the conversation ends. False if Gemini couldn't be reached (use the local voice)."""
        context = None
        try:
            context = await self.j._live_context()
        except Exception:
            pass
        last_err = ""
        for i in range(self.model_index, len(MODELS)):
            model = MODELS[i]
            try:
                async with self._client().aio.live.connect(model=model, config=self._config(context)) as session:
                    self.model_index = i
                    self.j.capabilities["live_voice"] = f"on ({model}, {self.cfg.get('voice.live.voice', DEFAULT_VOICE)})"
                    await self._talk(session, q, preroll or [])
                    return True
            except _Ended:
                return True
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"[:200]
                log.info("Gemini Live %s didn't connect (%s)", model, last_err)
                if _quota_or_auth(last_err):
                    break                         # another model won't help with a key or quota problem
        self._failed(_plain(last_err))
        return False

    async def _talk(self, session, q: asyncio.Queue, preroll: list) -> None:
        from google.genai import types

        j, speaker = self.j, self.j.speaker
        self._session = session
        state = {"speaking": False, "feed": None, "last_you": time.monotonic(), "last_him": 0.0,
                 "heard": [], "said": [], "busy": 0, "end": False}
        idle_after = float(self.cfg.get("voice.follow_up_after_answer", 6))
        running: set = set()                     # tool calls in flight
        first_wait = float(self.cfg.get("voice.live.first_wait", 6))
        aec = bool(j.echo_cancelled)
        bus.emit("state", state="listening")

        async def send_audio(frames) -> None:
            for f in frames:
                await session.send_realtime_input(
                    audio=types.Blob(data=np.asarray(f, dtype=np.int16).tobytes(), mime_type=f"audio/pcm;rate={IN_RATE}"))

        async def pump_mic() -> None:
            await send_audio(preroll)
            while not state["end"]:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=0.2)
                except TimeoutError:
                    continue
                speaking = speaker is not None and speaker.speaking
                if speaking and not aec:
                    continue                     # no echo canceller: he'd hear himself (wake word still interrupts)
                if j.vad is not None and j.vad.is_speech(frame):
                    state["last_you"] = time.monotonic()
                await send_audio([frame])

        def finish_feed() -> None:
            if state["feed"] is not None:
                state["feed"].close()
                state["feed"] = None

        async def run_tool(call) -> None:
            state["busy"] += 1
            try:
                result = await self._call(call.name, dict(call.args or {}))
            finally:
                state["busy"] -= 1
            resp = types.FunctionResponse(id=call.id, name=call.name, response={"result": result})
            if call.name == BRAIN_TOOL:
                resp.scheduling = types.FunctionResponseScheduling.WHEN_IDLE
            try:
                await session.send_tool_response(function_responses=[resp])
            except Exception as exc:
                log.info("couldn't return %s's result: %s", call.name, exc)

        async def listen_to_him() -> None:
            async for msg in session.receive():
                if msg.session_resumption_update and msg.session_resumption_update.new_handle:
                    self.resume_handle = msg.session_resumption_update.new_handle
                if msg.go_away is not None:
                    log.info("Gemini Live will close the connection soon: ending this conversation cleanly")
                    state["end"] = True
                if msg.tool_call and msg.tool_call.function_calls:
                    bus.emit("state", state="thinking")
                    for call in msg.tool_call.function_calls:
                        running.add(t := asyncio.create_task(run_tool(call)))
                        t.add_done_callback(running.discard)
                sc = msg.server_content
                if msg.data:
                    if state["feed"] is None and speaker is not None:
                        state["feed"] = speaker.stream_external(OUT_RATE)
                        bus.emit("state", state="speaking")
                    if state["feed"] is not None:
                        state["feed"].put(np.frombuffer(msg.data, dtype=np.int16).astype(np.float32) / 32768.0)
                    state["last_him"] = time.monotonic()
                if sc is None:
                    continue
                if sc.input_transcription and sc.input_transcription.text:
                    state["heard"].append(sc.input_transcription.text)
                    state["last_you"] = time.monotonic()
                if sc.output_transcription and sc.output_transcription.text:
                    state["said"].append(sc.output_transcription.text)
                if sc.interrupted:
                    # you started talking: stop at once and drop what he was about to say
                    if speaker is not None:
                        speaker.stop()
                    state["feed"] = None
                    self._flush_said(state, cut=True)
                    bus.emit("interrupted")
                    bus.emit("state", state="listening")
                if sc.turn_complete:
                    finish_feed()
                    self._flush_heard(state)
                    self._flush_said(state)
                    state["last_him"] = time.monotonic()
                    if not state["end"]:
                        bus.emit("state", state="listening", follow_up=True)
                if state["end"]:
                    return

        async def watch_idle() -> None:
            started = time.monotonic()
            while not state["end"]:
                await asyncio.sleep(0.25)
                now = time.monotonic()
                him = speaker is not None and speaker.speaking
                if him or state["busy"] or state["feed"] is not None:
                    continue
                quiet_since = max(state["last_you"], state["last_him"], started)
                limit = first_wait if not state["last_him"] else idle_after
                if now - quiet_since > limit:
                    state["end"] = True       # nobody's spoken for a while: the conversation is over

        tasks = [asyncio.create_task(c) for c in (pump_mic(), listen_to_him(), watch_idle())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t.exception() is not None and not state["end"]:
                    raise t.exception()
            # let him finish the sentence he's saying
            while speaker is not None and speaker.speaking and not state["end"]:
                await asyncio.sleep(0.1)
        finally:
            state["end"] = True
            self._session = None
            finish_feed()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._flush_heard(state)
            self._flush_said(state)
            bus.emit("state", state="idle")

    def _flush_heard(self, state: dict) -> None:
        text = " ".join(" ".join(state["heard"]).split())
        state["heard"] = []
        if text:
            bus.emit("transcript", text=text, source="voice")
            self._remember("user", text)

    def _flush_said(self, state: dict, cut: bool = False) -> None:
        text = " ".join("".join(state["said"]).split())
        state["said"] = []
        if text:
            bus.emit("reply", text=text + (" …" if cut else ""))
            self._remember("assistant", text)

    def _remember(self, role: str, text: str) -> None:
        mem = getattr(self.j, "memory", None)
        try:
            if mem is not None:
                mem.log_turn(getattr(self, "_session_id", None) or self._new_session(), role, text)
        except Exception:
            pass

    def _new_session(self) -> str:
        self._session_id = f"live-{uuid.uuid4()}"
        try:
            self.j.memory.start_session(self._session_id)
        except Exception:
            pass
        return self._session_id

    # ── tools ─────────────────────────────────────────────────────────────────────────────────────────
    async def _call(self, name: str, args: dict) -> str:
        """Run one of Gemini's tool calls. Returns plain text for Gemini to use."""
        from jarvis.tools.registry import registry

        if name == BRAIN_TOOL:
            return await self._ask_brain(str(args.get("task", "")))
        if name == CONFIRM_TOOL:
            pending = self.pending.pop(str(args.get("token", "")), None)
            bus.emit("confirm_result", approved=bool(args.get("approved")))
            if pending is None:
                return "Nothing is waiting for approval with that token."
            tool, targs, action = pending
            if not args.get("approved"):
                return f"Dropped: {action}. Don't do it unless they ask again."
            return await self._run(registry, tool, targs, approved=True)
        tool = registry._tools.get(name)
        if tool is not None and tool.confirm and getattr(self.j.ctx, "require_confirmation", True):
            action = tool.describe_action(args)
            token = uuid.uuid4().hex[:8]
            self.pending[token] = (name, args, action)
            bus.emit("confirm", action=action, token=token)
            return (f"NEEDS_APPROVAL token={token}: {action}. Ask them to confirm in one short question, then call "
                    f"{CONFIRM_TOOL}.")
        return await self._run(registry, name, args)

    async def _run(self, registry, name: str, args: dict, approved: bool = False) -> str:
        ctx = self.j.ctx
        saved = ctx.require_confirmation
        if approved:
            ctx.require_confirmation = False
        try:
            res = await registry.call(name, args, ctx)
        finally:
            ctx.require_confirmation = saved
        content = res.content
        if not isinstance(content, str):
            try:
                content = json.dumps(content, default=str)
            except Exception:
                content = str(content)
        return ("ERROR: " if res.is_error else "") + content[:6000]

    async def _ask_brain(self, task: str) -> str:
        """JARVIS's own brain (Claude Code on the plan) does the work; Gemini says the result."""
        if not task.strip():
            return "No task was given."
        brain = getattr(self.j, "brain", None)
        if brain is None:
            return "JARVIS's planning mind isn't available right now."
        bus.emit("tool", name=BRAIN_TOOL, status="start", args={"task": task[:120]}, category="brain")
        t0 = time.perf_counter()
        try:
            reply = await brain.respond(task, context=await self.j._live_context())
        except Exception as exc:
            log.exception("brain hand-off failed")
            bus.emit("tool", name=BRAIN_TOOL, status="error", ms=int((time.perf_counter() - t0) * 1000))
            return f"ERROR: the planning mind failed ({type(exc).__name__}). Say so briefly."
        bus.emit("tool", name=BRAIN_TOOL, status="done", ms=int((time.perf_counter() - t0) * 1000),
                 summary=str(reply)[:120])
        return str(reply)[:6000] or "Done."

    def approve_on_screen(self, approved: bool) -> bool:
        """The HUD's Yes/No for the latest action waiting on approval. True if one was waiting."""
        if not self.pending:
            return False
        token = list(self.pending)[-1]

        async def act() -> None:
            result = await self._call(CONFIRM_TOOL, {"token": token, "approved": approved})
            session = getattr(self, "_session", None)
            if session is not None:        # tell Gemini, so it can say how it went
                from google.genai import types

                note = f"(They {'approved' if approved else 'declined'} on screen. Result: {result[:1500]})"
                try:
                    await session.send_client_content(
                        turns=types.Content(role="user", parts=[types.Part(text=note)]), turn_complete=True)
                except Exception as exc:
                    log.info("couldn't tell Gemini about the on-screen answer: %s", exc)
        self._on_screen = asyncio.ensure_future(act())
        return True


class _Ended(Exception):
    pass


def _quota_or_auth(err: str) -> bool:
    e = err.lower()
    return any(s in e for s in ("api key", "api_key", "permission", "401", "403", "429", "quota", "exhausted"))


def _plain(err: str) -> str:
    e = err.lower()
    if "429" in e or "quota" in e or "exhausted" in e:
        return "free quota used up for now"
    if "api key" in e or "api_key" in e or "401" in e or "403" in e:
        return "the Gemini key was refused"
    if "name or service" in e or "connect" in e or "timed out" in e or "network" in e:
        return "no internet connection"
    return err or "couldn't connect"

