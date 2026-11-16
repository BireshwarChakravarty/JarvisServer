"""Talking to JARVIS through the iPhone / iPad.

The phone streams its microphone (16 kHz, 16-bit mono) over the companion link. Everything else
happens here on the Mac, with the same parts the Mac's own microphone uses:

  • "talk"  (the mic button): listen until you pause, transcribe with Whisper, answer, then keep
            listening for a follow-up, so a conversation needs no more taps. It ends after a few
            quiet seconds, or when you tap again.
  • "wake"  ("Hey JARVIS" while the companion is open): a wake-word detector of its own runs on
            the phone's audio; saying it starts a conversation as above.

His reply is spoken on the phone (or on the Mac, if you prefer). If the Mac and the phone both
hear "Hey JARVIS", whichever heard it first answers.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

import numpy as np

from jarvis.aio import spawn
from jarvis.voice.audio import FRAME, SAMPLE_RATE, VAD, drain, record_utterance

log = logging.getLogger("jarvis.remote.voice")

MAX_BUFFER_FRAMES = 400            # ~32 s of audio waiting to be processed
FOLLOW_UP_SECONDS = 7.0
FIRST_LISTEN_SECONDS = 8.0


class PhoneVoice:
    def __init__(self, jarvis, device: str, send: Callable[[dict], None], route_factory: Callable[[], Callable],
                 wake_factory: Callable[[], object | None]) -> None:
        self.j = jarvis
        self.device = device
        self.send = send                      # message to this phone
        self.route_factory = route_factory    # -> a speaker route that plays on this phone
        self.wake_factory = wake_factory      # -> a WakeWord (loaded) or None
        self.q: asyncio.Queue = asyncio.Queue(maxsize=MAX_BUFFER_FRAMES)
        self.mode = "off"
        self.speak_here = True
        self._carry = np.zeros(0, dtype=np.int16)
        self._task: asyncio.Task | None = None
        self._busy = False                    # a turn is running: incoming audio is his own voice/echo

    # ── audio in ─────────────────────────────────────────────────────────
    def feed(self, pcm: bytes) -> None:
        if self.mode == "off" or self._busy or len(pcm) < 2:
            return
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
        buf = np.concatenate([self._carry, samples])
        n = len(buf) // FRAME
        for k in range(n):
            frame = buf[k * FRAME:(k + 1) * FRAME].astype(np.int16)
            if self.q.full():
                try:
                    self.q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self.q.put_nowait(frame)
        self._carry = buf[n * FRAME:]

    # ── control ──────────────────────────────────────────────────────────
    def set_mode(self, mode: str, speak_here: bool = True) -> str | None:
        """Returns an error to show on the phone, or None."""
        self.speak_here = bool(speak_here)
        if mode not in ("off", "talk", "wake"):
            return "Unknown voice mode."
        if mode != "off" and (self.j is None or getattr(self.j, "stt", None) is None):
            return "Speech recognition isn't running on your Mac yet."
        if mode == self.mode and self._task and not self._task.done():
            return None
        self.stop()
        self.mode = mode
        if mode != "off":
            self._task = spawn(self._run(mode), name=f"phone-voice-{self.device}")
        return None

    def stop(self) -> None:
        self.mode = "off"
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self._busy = False
        drain(self.q)

    def _phase(self, phase: str, **extra) -> None:
        self.send({"type": "voice", "phase": phase, **extra})

    # ── the loops ────────────────────────────────────────────────────────
    async def _run(self, mode: str) -> None:
        try:
            if mode == "talk":
                heard = await self._conversation()
                self.mode = "off"
                self._phase("off", heard=heard)
                return
            wake = await asyncio.to_thread(self.wake_factory)
            if wake is None:
                self.mode = "off"
                self._phase("off", error="The “Hey JARVIS” detector isn't available on your Mac.")
                return
            while True:
                self._phase("wake")
                wake.reset()
                drain(self.q)
                while True:
                    frame = await self.q.get()
                    if await asyncio.to_thread(wake.process, frame):
                        break
                if not self.j.claim_voice(f"phone:{self.device}"):
                    continue                      # the Mac heard it first and is answering
                self.send({"type": "wake"})
                await self._conversation()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("phone voice loop failed")
            self.mode = "off"
            self._phase("off", error="Voice stopped after a problem on the Mac. Tap the mic to try again.")

    async def _conversation(self) -> bool:
        """Listen, answer, listen again... Returns whether anything was said."""
        from jarvis.core.orchestrator import DISMISS
        from jarvis.voice.stt import strip_wake_phrase

        vad = VAD()
        follow_up = False
        while True:
            drain(self.q)
            self._phase("listening", follow_up=follow_up)
            audio = await record_utterance(self.q, vad,
                                           start_timeout=FOLLOW_UP_SECONDS if follow_up else FIRST_LISTEN_SECONDS,
                                           end_silence=0.8, max_seconds=30.0)
            if audio is None:
                return follow_up                  # False: nobody spoke at all
            self._busy = True
            try:
                self._phase("transcribing")
                text = await asyncio.to_thread(self.j.stt.transcribe, audio)
                _, text = strip_wake_phrase(text)
                text = text.strip()
                self.send({"type": "heard", "text": text})
                if not text:
                    if follow_up:
                        return True
                    continue                      # nothing understood: keep listening once more
                if DISMISS.match(text):           # "that's all", "never mind": end quietly
                    self.send({"type": "dismissed"})
                    return True
                done = asyncio.get_running_loop().create_future()
                self.j.queue_request(text, source="phone", route=self._route(), done=done)
                self._phase("thinking")
                await done
                await asyncio.sleep(0.35)         # let the phone's speaker fall silent
            finally:
                self._busy = False
            follow_up = True

    def _route(self):
        return self.route_factory() if self.speak_here else None


def pcm_to_float(pcm: bytes) -> np.ndarray:
    """What the self-test feeds Whisper: the same conversion the phone's audio goes through."""
    return np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


__all__ = ["PhoneVoice", "pcm_to_float", "SAMPLE_RATE"]
