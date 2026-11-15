"""The orchestrator — JARVIS's nervous system.

Owns every subsystem and the voice state machine:

    standby ──"hey jarvis" / ✌️ / hold ⌥ / HUD──► listening ──► thinking ──► speaking
       ▲                                              ▲                        │
       └──────── silence ◄──── follow-up window ◄─────┴────────────────────────┘
                                   (say "Jarvis" or ✋ to interrupt at any time)

Also: greets you at login / unlock / when you sit down, runs proactive alerts,
asks for spoken confirmation before risky actions, and feeds the HUD.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import time
from typing import Any

from jarvis.aio import spawn
from jarvis.core.brain import Brain  # noqa: F401  (import registers the brain module)
from jarvis.core.briefing import gather, offline_extras
from jarvis.core.memory import Memory
from jarvis.core.persona import fallback_greeting, part_of_day
from jarvis.core.protocols import Protocols
from jarvis.core.scheduler import Scheduler
from jarvis.events import bus
from jarvis.tools.context import ToolContext
from jarvis.tools.registry import registry

log = logging.getLogger("jarvis")

YES = re.compile(r"\b(yes|yeah|yep|yup|sure|do it|go ahead|confirm(ed)?|proceed|affirmative|please do|ok(ay)?|"
                 r"absolutely|of course|send it|go for it|correct|right)\b", re.I)
NO = re.compile(r"\b(no|nope|nah|cancel|don'?t|stop|negative|wait|hold on|never ?mind|abort)\b", re.I)
DISMISS = re.compile(r"^(?:(?:that'?s (?:all|it)|that will be all|thanks?(?: you)?|cheers|never ?mind|nothing|"
                     r"no thanks?|go to sleep|stand ?by|dismissed)[\s,.!]*)+$", re.I)


def _human_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 3600:
        m = max(1, seconds // 60)
        return f"{m} minute{'s' if m != 1 else ''}"
    h = seconds // 3600
    if h < 24:
        return f"{h} hour{'s' if h != 1 else ''}"
    d = h // 24
    return f"{d} day{'s' if d != 1 else ''}"


def _log_failure(fut) -> None:
    """Background work started from other threads (arrival, departure, sentry…) must not fail silently."""
    try:
        exc = fut.exception()
    except Exception:          # cancelled
        return
    if exc is not None:
        log.error("background task failed", exc_info=exc)


class Jarvis:
    def __init__(self, cfg, headless: bool = False) -> None:
        self.cfg = cfg
        self.show_window = None   # set by the app shell: brings the HUD window to the front
        self.headless = headless
        self.loop: asyncio.AbstractEventLoop | None = None
        self.memory = Memory(cfg.db_path)
        self.protocols = Protocols(cfg.protocols_path)
        self.scheduler = Scheduler(self.memory, self.announce, cfg)
        self.ctx = ToolContext(cfg=cfg, memory=self.memory, scheduler=self.scheduler, protocols=self.protocols,
                               orchestrator=self, confirm=self.confirm,
                               require_confirmation=bool(cfg.get("safety.confirm_risky_actions", True)))
        import jarvis.tools.builtin  # noqa: F401  (registers tools)

        from jarvis.core.cli_brains import make_brain

        self.brain = make_brain(cfg, registry, self.memory, self.ctx)
        # subsystems (filled in by start())
        self.speaker = None
        self.mic = None
        self.wake = None
        self.vad = None
        self.stt = None
        self.camera = None
        self.faces = None
        self.presence = None
        self.gestures = None
        self.mcp = None
        self.ptt = None
        # state
        self.standby = False          # mic muted by voice command
        self.capabilities: dict[str, Any] = {}
        self._listen_now = asyncio.Event()
        self._interrupt = asyncio.Event()
        self._cancel_turn: asyncio.Event | None = None
        self._pending_confirm: asyncio.Future | None = None
        self._requests: asyncio.Queue = asyncio.Queue()
        self._greet_lock = asyncio.Lock()
        self._turn_active = False
        self._ptt_stop = asyncio.Event()
        self.ready = asyncio.Event()

    # ═════════════════════════════ startup ═══════════════════════════════
    def boot(self, msg: str, ok: bool | None = True) -> None:
        bus.emit("boot", message=msg, ok=ok)
        (log.info if ok is not False else log.warning)("boot: %s", msg)

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        bus.bind(self.loop)
        bus.emit("state", state="booting")
        cfg = self.cfg
        self.boot(f"J.A.R.V.I.S. core online · {len(registry)} abilities registered")

        # Voice
        if cfg.get("voice.enabled", True) and not self.headless:
            await self._start_voice()
        else:
            from jarvis.voice.tts import SilentTTS, Speaker

            self.speaker = Speaker(SilentTTS())
            self.speaker.start()
            self.boot("Voice disabled — text mode", None)

        # Vision
        if cfg.get("vision.enabled", True) and not self.headless:
            await self._start_vision()

        # MCP
        if cfg.get("mcp_servers"):
            from jarvis.tools.mcp_bridge import MCPBridge

            self.mcp = MCPBridge(dict(cfg.get("mcp_servers")))
            n = await self.mcp.start()
            self.brain.extra_tools = self.mcp.tools
            self.brain.extra_handlers = self.mcp.handlers
            self.boot(f"MCP bridge: {n} external tools", n > 0)

        import os

        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) \
            or cfg.get("brain.provider") in ("claude-code", "codex")
        provider = cfg.get("brain.provider", "anthropic")
        self.capabilities["brain"] = {
            "anthropic": cfg.get("brain.model"), "claude-code": "Claude plan (Claude Code)",
            "codex": "ChatGPT plan (Codex)", "ollama": "ollama"}.get(provider, provider)
        self.boot(f"Neural link: {self.capabilities['brain']}" + ("" if has_key else " (no API key found)"), True)
        self.scheduler.restore()
        bus.emit("capabilities", **self.capabilities)
        self.ready.set()

    async def _start_voice(self) -> None:
        cfg = self.cfg
        from jarvis.voice.audio import VAD, Microphone, PushToTalk, WakeWord
        from jarvis.voice.stt import Transcriber
        from jarvis.voice.tts import Speaker, make_backend

        tts_task = asyncio.to_thread(
            make_backend, cfg.get("voice.tts.backend", "auto"), cfg.get("voice.tts.voice", "bm_george"),
            float(cfg.get("voice.tts.speed", 1.0)), cfg.get("voice.tts.say_voice", "Daniel"), cfg.models_dir)
        self.wake = WakeWord(cfg.get("voice.wake_word", "hey_jarvis"), float(cfg.get("voice.wake_threshold", 0.5)),
                             models_dir=cfg.models_dir)
        self.stt = Transcriber(cfg.get("voice.stt.backend", "auto"), cfg.get("voice.stt.model", "small.en"),
                               cfg.get("voice.stt.language", "en"), [cfg.get("owner.name", "")],
                               models_dir=cfg.models_dir)
        backend, wake_ok, stt_name = await asyncio.gather(
            tts_task, asyncio.to_thread(self.wake.load), asyncio.to_thread(self._load_stt))
        self.speaker = Speaker(backend, cfg.get("voice.output_device"))
        self.speaker.start()
        self.capabilities.update(tts=backend.name, wake_word=wake_ok, stt=stt_name)
        self.boot(f"Voice synthesis: {backend.name}", backend.name != "silent")
        self.boot(f"Speech recognition: {stt_name or 'unavailable'}", bool(stt_name))
        self.boot("Wake word: 'Hey Jarvis'" if wake_ok else "Wake word: transcript fallback", wake_ok)
        self.vad = VAD()
        try:
            self.mic = Microphone(cfg.get("voice.input_device"))
            self.mic.start(self.loop)
            self.capabilities["microphone"] = True
            self.boot("Microphone array online")
        except Exception as exc:
            self.mic = None
            self.capabilities["microphone"] = False
            self.boot(f"Microphone unavailable: {exc}", False)
        key = cfg.get("voice.push_to_talk_key")
        if key and self.mic:
            self.ptt = PushToTalk(key, self._ptt_press, self._ptt_release)
            if os.environ.get("JARVIS_APP") == "1":
                # the app installs a native NSEvent monitor on the main thread (see app/menubar.py)
                self.boot("Push-to-talk: hold right ⌥")
            elif self.ptt.start():
                self.boot(f"Push-to-talk: hold {key}")

    def _load_stt(self) -> str | None:
        try:
            name = self.stt.load()
            self.stt.warmup()
            return name
        except Exception as exc:
            log.warning("STT unavailable: %s", exc)
            return None

    async def _start_vision(self) -> None:
        cfg = self.cfg
        from jarvis.vision.camera import Camera
        from jarvis.vision.faces import FaceEngine
        from jarvis.vision.gestures import GestureEngine
        from jarvis.vision.presence import Presence

        self.camera = Camera(int(cfg.get("vision.camera_index", 0)), int(cfg.get("vision.fps", 15)))
        ok = await asyncio.to_thread(self.camera.start)
        self.capabilities["camera"] = ok
        if not ok:
            self.boot(f"Optical sensors offline: {self.camera.error}", False)
            return
        self.boot("Optical sensors online")
        self.ctx.camera = self.camera
        owner = cfg.get("owner.name", "owner")
        if cfg.get("vision.face_recognition", True):
            self.faces = FaceEngine(cfg.models_dir, cfg.faces_dir, float(cfg.get("vision.recognition_threshold", 0.42)),
                                    owner_name=owner)
            if await asyncio.to_thread(self.faces.load):
                self.ctx.faces = self.faces
                self.presence = Presence(
                    owner, away_after=60 * float(cfg.get("vision.away_minutes", 10)),
                    on_arrive=lambda away: self._threadsafe(self.on_arrival(away)),
                    on_leave=lambda: self._threadsafe(self.on_departure()),
                    on_stranger=lambda: self._threadsafe(self.on_stranger()),
                )
                self.presence.recognition = owner in self.faces.known
                self.ctx.presence = self.presence

                def face_proc(frame):
                    before = self.faces._last
                    self.faces.process(frame)
                    if self.faces._last != before:
                        self.presence.update(self.faces.snapshot())

                self.camera.add_processor(face_proc)
                enrolled = owner in self.faces.known
                self.capabilities["faces"] = True
                self.boot(f"Facial recognition: {'tracking ' + owner if enrolled else 'online (run `jarvis enroll`)'}",
                          enrolled or None)
        if cfg.get("vision.gestures", True):
            self.gestures = GestureEngine(cfg.models_dir, self._on_gesture_thread, on_motion=self._on_motion_thread,
                                          idle_off=60 * float(cfg.get("vision.hand_control_idle_minutes", 10)))
            if await asyncio.to_thread(self.gestures.load):
                self.camera.add_processor(self.gestures.process)
                self.capabilities["gestures"] = True
                self.boot("Hand control ready · off until you ask")

    def _threadsafe(self, coro) -> None:
        if self.loop:
            fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
            fut.add_done_callback(_log_failure)

    async def _supervise(self, name: str, factory) -> None:
        """Keep a core loop alive: if it crashes on something unexpected, log it and restart it with a
        growing pause, instead of letting one failure take the whole assistant down."""
        delay = 1.0
        while True:
            started = time.monotonic()
            try:
                await factory()
                return                      # finished on purpose
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s loop crashed; restarting in %.0fs", name, delay)
                bus.emit("error", message=f"Internal hiccup in {name}; recovering.")
            if time.monotonic() - started > 60:
                delay = 1.0                 # it ran fine for a while: start the backoff over
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    # ═════════════════════════════ main loop ═════════════════════════════
    async def run(self, greet: bool = True) -> None:
        await self.start()
        sup = self._supervise
        tasks = [
            asyncio.create_task(sup("requests", self._request_worker), name="requests"),
            asyncio.create_task(sup("scheduler", self.scheduler.watch), name="watch"),
            asyncio.create_task(sup("telemetry", self._stats_loop), name="stats"),
        ]
        if self.mic is not None:
            tasks.append(asyncio.create_task(sup("voice", self._voice_loop), name="voice"))
        from jarvis.mac.session import SessionWatcher

        watcher = SessionWatcher(on_unlock=self.on_unlock, on_wake=self.on_wake)
        tasks.append(asyncio.create_task(sup("session", watcher.run), name="session"))
        bus.emit("state", state="idle")
        if self.speaker:
            self.speaker.play_tone("boot")
        if greet and self.cfg.get("greeting.on_login", True):
            asyncio.create_task(self.greet("login")).add_done_callback(_log_failure)
        try:
            await asyncio.gather(*tasks)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        for comp in (self.mic, self.camera):
            try:
                if comp:
                    comp.stop()
            except Exception:
                pass
        if self.mcp:
            await self.mcp.close()
        self.brain.reset()
        await asyncio.sleep(0)

    # ═════════════════════════════ voice ═════════════════════════════════
    def trigger_listen(self) -> None:
        if self.speaker and self.speaker.speaking:
            self.interrupt()
        self._listen_now.set()

    def ptt_event(self, down: bool) -> None:
        """Called from the app's main-thread key monitor."""
        if self.ptt is None:
            return
        if down and not self.ptt.down:
            self.ptt.down = True
            self._ptt_press()
        elif not down and self.ptt.down:
            self.ptt.down = False
            self._ptt_release()

    def _ptt_press(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self._ptt_start)

    def _ptt_release(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self._ptt_stop.set)

    def _ptt_start(self) -> None:
        self._ptt_stop.clear()
        self.trigger_listen()

    async def _voice_loop(self) -> None:
        from jarvis.voice.audio import drain

        q = self.mic.subscribe()
        while True:
            try:
                if self.standby_blocks():
                    await asyncio.sleep(0.2)
                    drain(q)
                    continue
                bus.emit("state", state="idle")
                first_text = await self._wait_for_trigger(q)
                await self._converse(q, first_text)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("voice loop error")
                await asyncio.sleep(1)

    def standby_blocks(self) -> bool:
        return self.mic is None or self.mic.muted

    async def _wait_for_trigger(self, q: asyncio.Queue) -> str | None:
        """Block until the wake word / PTT / gesture / HUD. Returns any command already spoken."""
        from jarvis.voice.audio import record_utterance
        from jarvis.voice.stt import strip_wake_phrase

        self._listen_now.clear()
        while True:
            if self._listen_now.is_set():
                self._listen_now.clear()
                self.speaker.play_tone("wake")
                return None
            if self.wake and self.wake.available:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                if self.wake.process(frame):
                    self.speaker.play_tone("wake")
                    return None
            else:
                # Fallback: transcribe short bursts of speech and look for "Jarvis"
                listen_task = asyncio.create_task(record_utterance(q, self.vad, start_timeout=3600,
                                                                   end_silence=0.6, max_seconds=8))
                trigger_task = asyncio.create_task(self._listen_now.wait())
                done, pending = await asyncio.wait({listen_task, trigger_task},
                                                   return_when=asyncio.FIRST_COMPLETED)
                for p in pending:
                    p.cancel()
                if trigger_task in done:
                    continue
                audio = listen_task.result()
                if audio is None or not self.stt:
                    continue
                text = await asyncio.to_thread(self.stt.transcribe, audio)
                woke, rest = strip_wake_phrase(text)
                if woke:
                    bus.emit("wake", score=1.0)
                    self.speaker.play_tone("wake")
                    return rest or None

    async def _converse(self, q: asyncio.Queue, first_text: str | None) -> None:
        from jarvis.voice.audio import drain, record_utterance
        from jarvis.voice.stt import strip_wake_phrase

        follow_up = False
        pending_text = first_text
        while True:
            if pending_text:
                text, pending_text = pending_text, None
            else:
                bus.emit("state", state="listening", follow_up=follow_up)
                stop_evt = self._ptt_stop if (self.ptt and self.ptt.down) else None
                audio = await record_utterance(
                    q, self.vad,
                    start_timeout=float(self.cfg.get("voice.follow_up_seconds", 7)) if follow_up else 6.0,
                    end_silence=0.75 if not stop_evt else 30.0,
                    max_seconds=float(self.cfg.get("voice.max_utterance_seconds", 30)),
                    stop_event=stop_evt,
                )
                if audio is None:
                    bus.emit("state", state="idle")
                    return
                bus.emit("state", state="transcribing")
                text = await asyncio.to_thread(self.stt.transcribe, audio) if self.stt else ""
                _, text = strip_wake_phrase(text)
            if not text:
                if follow_up:
                    return
                bus.emit("state", state="idle")
                return
            if DISMISS.match(text.strip()):
                bus.emit("transcript", text=text, source="voice")
                self.speaker.enqueue(f"Very good, {self.cfg.get('owner.title', 'sir')}.")
                return
            turn = asyncio.create_task(self.handle_text(text, source="voice"))
            outcome = await self._monitor_barge_in(q, turn)
            if outcome == "stopped":
                return
            follow_up = True
            if outcome == "barge":
                continue  # user interrupted with the wake word: listen right away
            await asyncio.sleep(0.25)  # let the room's echo die down
            drain(q)

    async def _monitor_barge_in(self, q: asyncio.Queue, turn: asyncio.Task) -> str:
        """While JARVIS thinks/talks, watch for the wake word or a ✋ to interrupt."""
        self._interrupt.clear()
        barge = bool(self.cfg.get("voice.barge_in", True)) and self.wake is not None and self.wake.available
        if self.wake:
            self.wake.reset()
        while not (turn.done() and not self.speaker.speaking):
            if self._interrupt.is_set():
                self._interrupt.clear()
                return "stopped"
            try:
                frame = await asyncio.wait_for(q.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if barge and self.speaker.speaking and self.wake.process(frame) and not self.speaker.saying("jarvis"):
                # (ignored while he's saying "Jarvis" himself: that's his own voice in the mic)
                self.interrupt()
                self.speaker.play_tone("wake")
                return "barge"
        if turn.done() and turn.exception():
            log.error("turn failed: %s", turn.exception())
        return "done"

    def interrupt(self) -> None:
        """Stop talking and abandon the current turn."""
        if self.speaker:
            self.speaker.stop()
        if self._cancel_turn is not None:
            self._cancel_turn.set()
        bus.emit("interrupted")

    # ═════════════════════════════ turns ═════════════════════════════════
    async def handle_text(self, text: str, source: str = "text", images: list | None = None) -> str:
        bus.emit("transcript", text=text, source=source)
        cancel = asyncio.Event()
        self._cancel_turn = cancel
        self._turn_active = True

        first = asyncio.Event()

        async def on_sentence(sentence: str) -> None:
            first.set()
            bus.emit("state", state="speaking")
            if self.speaker:
                self.speaker.enqueue(sentence)

        filler = asyncio.create_task(self._filler(first, cancel)) if (
            self.speaker and source in ("voice", "hud", "protocol") and self.cfg.get("voice.tts.fillers", True)) else None
        try:
            reply = await self.brain.respond(text, on_sentence=on_sentence, context=await self._live_context(),
                                             images=images, cancel=cancel)
        except asyncio.CancelledError:
            raise
        except Exception:
            # never leave you in silence: say so, and keep everything else running
            log.exception("turn failed for %r", text[:80])
            reply = f"Sorry, {self.cfg.get('owner.title', 'sir')}, something went wrong on my side. Could you try that again?"
            bus.emit("reply", text=reply)
            if self.speaker:
                self.speaker.enqueue(reply)
        finally:
            self._turn_active = False
            if filler:
                filler.cancel()
        return reply

    # A person answers within a second or so, even if only to say they're on it. When the model is
    # slow to its first word (a tool call, a web search) JARVIS says so instead of leaving silence.
    FILLERS = ["One moment, {t}.", "Right away, {t}.", "Just a moment.", "Bear with me, {t}.", "Let me see.",
               "Give me a second, {t}.", "On it."]
    STILL = ["Still working on it, {t}.", "Nearly there.", "This is taking a little longer than I'd like, {t}."]

    async def _filler(self, first: asyncio.Event, cancel: asyncio.Event) -> None:
        import random

        t = self.cfg.get("owner.title", "sir")
        for delay, lines in ((1.3, self.FILLERS), (9.0, self.STILL)):
            try:
                await asyncio.wait_for(first.wait(), delay)
                return
            except asyncio.TimeoutError:
                pass
            if cancel.is_set() or (self.speaker and self.speaker.speaking):
                return
            pool = [x for x in lines if x != getattr(self, "_last_filler", None)]
            self._last_filler = line = random.choice(pool)
            self.speaker.enqueue(line.format(t=t))
            bus.emit("filler", text=line.format(t=t))

    def queue_request(self, text: str, source: str = "protocol") -> None:
        self._requests.put_nowait((text, source))

    async def _request_worker(self) -> None:
        while True:
            text, source = await self._requests.get()
            try:
                if self.speaker:
                    await self.speaker.wait_done(timeout=60)
                await self.handle_text(text, source=source)
                if self.speaker:
                    await self.speaker.wait_done(timeout=120)
                if source in ("hud", "protocol") and self.mic is not None and not self.mic.muted:
                    self._listen_now.set()  # allow a spoken follow-up
            except Exception:
                log.exception("request failed")
            finally:
                bus.emit("state", state="idle")

    async def _live_context(self) -> dict:
        ctx: dict[str, Any] = {}
        try:
            from jarvis.mac import system as mac

            if mac.IS_MAC:
                front, batt = await asyncio.wait_for(asyncio.gather(
                    asyncio.to_thread(mac.frontmost_app), asyncio.to_thread(mac.battery)), timeout=1.5)
                ctx["frontmost app"] = front
                if batt.get("percent") is not None:
                    ctx["battery"] = f"{batt['percent']}% {batt.get('state', '')}".strip()
        except Exception:
            pass
        if self.faces is not None and self.faces.available:
            names = [f.name or "unknown person" for f in self.faces.snapshot()]
            ctx["camera sees"] = ", ".join(names) if names else "nobody"
        ctx["location"] = self.cfg.get("location.city")
        alarms = self.scheduler.list()
        if alarms:
            ctx["active timers"] = ", ".join(f"{a['message']} ({max(0, a['in_seconds'])}s)" for a in alarms[:3])
        return ctx

    # ═════════════════════════════ confirmation ══════════════════════════
    async def confirm(self, action: str) -> bool:
        """Ask before doing something consequential. Voice, ✋👍 gesture or HUD button."""
        from jarvis.voice.audio import record_utterance

        title = self.cfg.get("owner.title", "sir")
        confirm_id = f"c{int(time.time() * 1000)}"
        fut: asyncio.Future = self.loop.create_future()
        self._pending_confirm = fut
        bus.emit("confirm", id=confirm_id, action=action)
        question = f"Shall I {action}?" if len(action) < 160 else f"Shall I go ahead? I'm about to {action[:150]}."
        if self.speaker:
            self.speaker.enqueue(question)
            await self.speaker.wait_done(timeout=30)

        voice_task = None
        if self.mic is not None and self.stt is not None and not self.mic.muted:
            async def by_voice() -> bool | None:
                q = self.mic.subscribe()
                try:
                    bus.emit("state", state="listening", confirm=True)
                    audio = await record_utterance(q, self.vad, start_timeout=8, end_silence=0.6, max_seconds=6)
                finally:
                    self.mic.unsubscribe(q)
                if audio is None:
                    return None
                reply = await asyncio.to_thread(self.stt.transcribe, audio)
                bus.emit("transcript", text=reply, source="voice")
                if NO.search(reply):
                    return False
                if YES.search(reply):
                    return True
                return None

            voice_task = asyncio.create_task(by_voice())

        try:
            waiters = {fut} | ({voice_task} if voice_task else set())
            done, _ = await asyncio.wait(waiters, timeout=20, return_when=asyncio.FIRST_COMPLETED)
            answer = None
            if fut in done:
                answer = fut.result()
            elif voice_task in done:
                answer = voice_task.result()
                if answer is None and not fut.done():  # unclear speech: give the HUD/gesture a moment
                    try:
                        answer = await asyncio.wait_for(asyncio.shield(fut), timeout=6)
                    except asyncio.TimeoutError:
                        answer = None
        finally:
            if voice_task and not voice_task.done():
                voice_task.cancel()
            self._pending_confirm = None
        approved = bool(answer)
        bus.emit("confirm_result", id=confirm_id, approved=approved)
        if not approved and self.speaker:
            self.speaker.enqueue("Understood. Standing down." if answer is False else
                                 f"I'll take that as a no, {title}.")
        return approved

    def resolve_confirmation(self, approved: bool) -> None:
        if self._pending_confirm is not None and not self._pending_confirm.done():
            self._pending_confirm.set_result(approved)

    # ═════════════════════════════ greetings & presence ══════════════════
    async def greet(self, kind: str = "login", away_for: float | None = None) -> None:
        async with self._greet_lock:
            last = self.memory.get("last_greeting", 0) or 0
            gap = 60 * float(self.cfg.get("greeting.min_gap_minutes", 45))
            if kind != "login" and time.time() - last < gap:
                return
            if kind == "login" and time.time() - last < 120:  # restarts in quick succession
                return
            self.memory.set("last_greeting", time.time())
            self.memory.log_event("greeting", occasion=kind)
            await self.ready.wait()
            bus.emit("state", state="thinking", greeting=kind)
            title = self.cfg.get("owner.title", "sir")
            data = await gather(self.cfg, self.memory, "login" if kind == "login" else "return") \
                if self.cfg.get("greeting.briefing", True) else {"part_of_day": part_of_day()}
            bus.emit("briefing", data=data)
            # Fresh every time: a different angle, what changed, and his own recent lines to avoid
            from jarvis.core import greeting_style as gs

            data = gs.trim(data)
            recent = self.memory.get(gs.RECENT_KEY, []) or []
            delta = gs.changes(data, self.memory.get(gs.SNAPSHOT_KEY))
            since = time.time() - last if last else None
            angle = gs.pick_angle(data, delta, away_for if away_for is not None else since, recent)
            note = (gs.instructions(title, "login" if kind == "login" else "return", data, delta, angle, recent,
                                    _human_duration(away_for or since or 0))
                    + " Briefing data: " + json.dumps(data, default=str))
            spoken: list[str] = []

            async def on_sentence(s: str) -> None:
                spoken.append(s)
                bus.emit("state", state="speaking")
                if self.speaker:
                    self.speaker.enqueue(s)

            reply = await self.brain.respond(None, event_note=note, on_sentence=on_sentence,
                                             context=await self._live_context())
            if not reply or reply.startswith(("My Anthropic API key", "I can't reach", "Something went wrong",
                                              "My servers", "Claude Code", "Codex", "I'm not signed in",
                                              "We've reached", "I lost my connection")):
                line = fallback_greeting(self.cfg.get("owner.name", ""), title,
                                         "login" if kind == "login" else "return", extras=offline_extras(data))
                bus.emit("reply", text=line)
                if self.speaker:
                    self.speaker.stop()
                    self.speaker.enqueue(line)
                reply = line
            try:
                gs.remember(self.memory, " ".join(spoken) or reply or "", angle, data)
            except Exception:
                log.debug("couldn't record greeting", exc_info=True)
            if self.speaker:
                await self.speaker.wait_done(timeout=90)
            if self.mic is not None and not self.mic.muted:
                self._listen_now.set()  # answer the "what shall we do today?" without the wake word

    def _return_kind(self, away_for: float) -> str:
        """First time at the Mac today (or after a long absence) earns the full daily briefing."""
        last = self.memory.get("last_greeting", 0) or 0
        new_day = dt.datetime.fromtimestamp(last).date() != dt.date.today()
        return "login" if new_day or away_for >= 4 * 3600 else "return"

    async def on_unlock(self, away_for: float) -> None:
        if not self.cfg.get("greeting.on_unlock", True):
            return
        bus.emit("session_event", event="unlock", away_for=int(away_for))
        if away_for >= 60 * float(self.cfg.get("vision.away_minutes", 10)):
            await self.greet(self._return_kind(away_for), away_for)

    async def on_wake(self, slept_for: float) -> None:
        bus.emit("session_event", event="wake", slept_for=int(slept_for))

    async def on_arrival(self, away_for: float) -> None:
        self.memory.log_event("arrival", away_for=away_for)
        from jarvis.mac.session import screen_locked

        if screen_locked():
            # Back at a locked Mac: light the screen so it's ready for Touch ID / Apple Watch / password.
            # JARVIS never types your password; a webcam match is not strong enough to unlock with.
            if self.cfg.get("vision.wake_on_return", True):
                from jarvis.mac.system import wake_display

                await asyncio.to_thread(wake_display)
                if self.speaker and self.cfg.get("vision.greet_on_arrival", True):
                    self.speaker.enqueue(f"Welcome back, {self.cfg.get('owner.title', 'sir')}.")
            return   # the full briefing follows when you unlock (on_unlock)
        if away_for >= 60 * float(self.cfg.get("vision.away_minutes", 10)) and self.cfg.get("vision.greet_on_arrival", True):
            await self.greet(self._return_kind(away_for), away_for)

    async def on_departure(self) -> None:
        self.memory.log_event("departure")
        if not self.cfg.get("vision.auto_lock_when_away", False):
            return
        from jarvis.mac.session import idle_seconds, screen_locked
        from jarvis.mac.system import lock_screen

        if screen_locked():
            return
        idle = idle_seconds()
        if idle is not None and idle < 15:
            # someone is typing or moving the mouse right now: you're here, just out of the camera's view
            log.info("walk-away lock skipped: keyboard/mouse active %.0fs ago", idle)
            return
        try:
            result = await asyncio.to_thread(lock_screen)
            log.info("walk-away lock: %s", result)
            bus.emit("announce", text="Locked the Mac: you walked away.", priority="normal")
        except Exception as exc:
            log.warning("walk-away lock failed: %s", exc)
            bus.emit("alert", message=f"Couldn't lock the Mac: {exc}")

    async def on_stranger(self) -> None:
        if not self.cfg.get("vision.sentry_mode", False) or self.camera is None:
            return
        data = await asyncio.to_thread(self.camera.jpeg, 90)
        if data:
            path = self.cfg.snapshots_dir / f"stranger-{dt.datetime.now():%Y%m%d-%H%M%S}.jpg"
            path.write_bytes(data)
            self.memory.log_event("stranger", snapshot=str(path))
            bus.emit("alert", message="Unrecognised person at your Mac", snapshot=str(path))
            try:
                from jarvis.mac.system import notify

                await asyncio.to_thread(notify, "JARVIS · Sentry", "An unrecognised person is at your Mac.")
            except Exception:
                pass

    # ═════════════════════════════ announcements ═════════════════════════
    async def announce(self, text: str, priority: str = "normal") -> None:
        bus.emit("announce", text=text, priority=priority)
        from jarvis.mac.session import screen_locked

        locked = screen_locked()
        quiet = self.mic is not None and self.mic.muted and priority != "high"
        away = self.presence is not None and not self.presence.present and priority != "high"
        if locked or quiet or away or self.speaker is None:
            try:
                from jarvis.mac.system import notify

                await asyncio.to_thread(notify, "JARVIS", text)
            except Exception:
                pass
            if priority != "high" or self.speaker is None:
                return
        while self._turn_active:
            await asyncio.sleep(0.3)
        await self.speaker.wait_done(timeout=60)
        self.speaker.play_tone("done")
        await asyncio.sleep(0.3)
        self.speaker.enqueue(text)

    async def say(self, text: str) -> None:
        bus.emit("reply", text=text)
        if self.speaker:
            self.speaker.enqueue(text)

    # ═════════════════════════════ control & gestures ════════════════════
    async def control(self, action: str) -> str:
        if action == "standby":
            self._listen_now.clear()
            return "Standing by. Say 'Hey Jarvis' when you need me."
        if action == "mute_mic":
            if self.mic:
                self.mic.muted = True
            bus.emit("mic", muted=True)
            return "Microphone muted. Unmute me from the HUD or the menu."
        if action == "unmute_mic":
            if self.mic:
                self.mic.muted = False
            bus.emit("mic", muted=False)
            return "Microphone back on."
        if action == "new_conversation":
            asyncio.get_running_loop().call_later(0.5, self.brain.reset)
            return "Fresh start. This conversation will be forgotten after this reply."
        if action == "quiet":
            asyncio.get_running_loop().call_later(0.2, self.interrupt)
            return "OK."
        return f"Unknown action {action}"

    def set_hand_control(self, on: bool) -> str:
        """Hand control (poses, swipes, pinch dial, air mouse) runs only when you ask for it."""
        g = self.gestures
        if g is None or not g.available:
            return ("Hand control needs the camera and the gesture model; neither is running right now."
                    if on else "Hand control is already off.")
        if bool(on) == g.enabled:
            return "Hand control is already on." if on else "Hand control is already off."
        g.set_enabled(on)
        if on:
            mins = int(g.idle_off // 60)
            return (f"Hand control on. Show me your hand. Hold up one finger for the pointer. "
                    f"I'll switch it off after {mins} minutes without seeing a hand.")
        return "Hand control off."

    def _on_gesture_thread(self, gesture: str, action: str) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self._on_gesture, gesture, action)

    def _on_gesture(self, gesture: str, action: str) -> None:
        if action == "yes":
            self.resolve_confirmation(True)
        elif action == "no":
            self.resolve_confirmation(False)
        elif action == "stop":
            if self._pending_confirm is not None:
                self.resolve_confirmation(False)
            elif (self.speaker and self.speaker.speaking) or self._turn_active:
                self.interrupt()
                self._interrupt.set()
            else:
                spawn(registry.call("media_control", {"action": "pause"}, self.ctx))
        elif action == "listen":
            self.trigger_listen()
        elif action == "media_toggle":
            spawn(registry.call("media_control", {"action": "toggle"}, self.ctx))
        elif action in ("next", "previous"):
            spawn(registry.call("media_control", {"action": action}, self.ctx))
        elif action == "show_hud":
            if self.show_window:
                self.show_window()

    # pinch-and-drag: vertical is a volume dial, sideways spins the hologram (the HUD does that part)
    def _on_motion_thread(self, ev) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: spawn(self._on_motion(ev)))

    async def _on_motion(self, ev) -> None:
        from jarvis.mac import system as mac

        if ev.kind == "pinch_start":
            self._dial = {"start": None, "sent": None, "t": 0.0}
            try:
                self._dial["start"] = await asyncio.to_thread(mac.get_volume)
            except Exception:
                self._dial["start"] = 50
            bus.emit("dial", phase="start", value=self._dial["start"])
            return
        dial = getattr(self, "_dial", None)
        if dial is None or dial["start"] is None:
            return
        if ev.kind == "pinch_drag" and ev.direction == "x":
            bus.emit("spin", dx=round(ev.dx, 4))
        elif ev.kind in ("pinch_drag", "pinch_end") and ev.direction == "y":
            level = int(max(0, min(100, round(dial["start"] + ev.total * 160))))   # a hand-span ≈ the full range
            now = time.monotonic()
            if level != dial["sent"] and (ev.kind == "pinch_end" or now - dial["t"] > 0.12):
                dial["sent"], dial["t"] = level, now
                bus.emit("dial", phase="end" if ev.kind == "pinch_end" else "move", value=level)
                try:
                    await asyncio.to_thread(mac.set_volume, level)
                except Exception:
                    log.debug("volume dial failed", exc_info=True)
        if ev.kind == "pinch_end":
            if ev.direction != "y":
                bus.emit("dial", phase="end", value=None)
            self._dial = None

    # ═════════════════════════════ HUD commands ══════════════════════════
    async def hud_command(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "text" and msg.get("text", "").strip():
            self.queue_request(msg["text"].strip(), source="hud")
        elif kind == "listen":
            self.trigger_listen()
        elif kind == "stop":
            self.interrupt()
            self._interrupt.set()
        elif kind == "confirm":
            self.resolve_confirmation(bool(msg.get("approved")))
        elif kind == "mute":
            await self.control("mute_mic" if msg.get("muted") else "unmute_mic")
        elif kind == "greet":
            self.memory.set("last_greeting", 0)
            asyncio.create_task(self.greet("login")).add_done_callback(_log_failure)
        elif kind == "hand_control":
            on = msg.get("on")
            if on is None and self.gestures is not None:
                on = not self.gestures.enabled
            reply = self.set_hand_control(bool(on))
            bus.emit("announce", text=reply, priority="normal")
        elif kind == "protocol" and msg.get("name"):
            self.queue_request(f"Run the {msg['name']} protocol.", source="hud")

    # ═════════════════════════════ telemetry for the HUD ═════════════════
    async def _stats_loop(self) -> None:
        import psutil

        last_net = psutil.net_io_counters()
        last_t = time.time()
        tick = 0
        while True:
            await asyncio.sleep(2)
            tick += 1
            now = time.time()
            net = psutil.net_io_counters()
            dt_s = max(0.1, now - last_t)
            stats = {
                "cpu": psutil.cpu_percent(),
                "mem": psutil.virtual_memory().percent,
                "disk": psutil.disk_usage("/").percent,
                "net_down": (net.bytes_recv - last_net.bytes_recv) / dt_s,
                "net_up": (net.bytes_sent - last_net.bytes_sent) / dt_s,
            }
            last_net, last_t = net, now
            if tick % 15 == 1:  # every 30 s
                try:
                    from jarvis.mac.system import battery

                    b = await asyncio.to_thread(battery)
                    stats["battery"] = b.get("percent")
                    stats["charging"] = "charg" in (b.get("state") or "") and "dis" not in (b.get("state") or "")
                except Exception:
                    pass
            bus.emit("stats", **stats)
            if tick % 150 == 1:  # every 5 min: weather + agenda panels
                spawn(self._refresh_panels())

    async def _refresh_panels(self) -> None:
        from jarvis.core.briefing import _events_today, _weather

        try:
            w = await asyncio.wait_for(asyncio.to_thread(_weather, self.cfg), 10)
            bus.emit("weather", **w)
        except Exception:
            pass
        try:
            events = await asyncio.wait_for(asyncio.to_thread(_events_today), 15)
            bus.emit("agenda", events=events)
        except Exception:
            pass
        bus.emit("alarms", alarms=self.scheduler.list())
        bus.emit("protocols", items=self.protocols.list())
