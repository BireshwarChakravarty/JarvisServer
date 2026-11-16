"""Microphone capture, wake-word detection and voice-activity endpointing.

Pipeline (16 kHz mono int16, 80 ms frames):

    mic ─► frames ─┬─► wake word (openWakeWord "hey jarvis")  ─► wake / barge-in
                   └─► VAD endpointer (webrtcvad or energy)     ─► utterance audio
"""
from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Callable, Optional

import numpy as np

from jarvis.events import bus

log = logging.getLogger("jarvis.audio")

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms — what openWakeWord expects


def _find_device(fragment: str | None, kind: str) -> int | None:
    if not fragment:
        return None
    import sounddevice as sd

    for idx, dev in enumerate(sd.query_devices()):
        channels = dev["max_input_channels"] if kind == "input" else dev["max_output_channels"]
        if channels > 0 and fragment.lower() in dev["name"].lower():
            return idx
    log.warning("No %s device matching %r — using default", kind, fragment)
    return None


class Microphone:
    """Continuous capture; fans frames out to any number of asyncio subscribers."""

    def __init__(self, device: str | None = None) -> None:
        self.device = device
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: list[asyncio.Queue] = []
        self._last_level = 0.0
        self.muted = False

    def subscribe(self, maxsize: int = 400) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        import sounddevice as sd

        self._loop = loop
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME,
            device=_find_device(self.device, "input"), callback=self._callback,
        )
        self._stream.start()
        log.info("microphone started (%s)", self._stream.device)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, indata, frames, time_info, status) -> None:  # audio thread
        if self.muted:
            return
        frame = indata[:, 0].copy()
        now = time.monotonic()
        if now - self._last_level > 0.05:
            self._last_level = now
            rms = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))
            bus.emit("level", source="mic", value=min(1.0, rms * 8))
        loop = self._loop
        if loop is None:
            return
        for q in list(self._queues):
            loop.call_soon_threadsafe(_put_drop_oldest, q, frame)


def _put_drop_oldest(q: asyncio.Queue, item) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
            q.put_nowait(item)
        except Exception:
            pass


def drain(q: asyncio.Queue) -> None:
    while not q.empty():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            break


# ═══════════════════════════════ wake word ═══════════════════════════════════
class WakeWord:
    """openWakeWord detector. Ships a pre-trained "hey jarvis" model."""

    def __init__(self, model_name: str = "hey_jarvis", threshold: float = 0.5, models_dir=None,
                 announce: bool = True) -> None:
        self.model_name = model_name
        self.announce = announce        # False for the phone's detector: the Mac's HUD shouldn't react
        self.models_dir = models_dir
        self.threshold = threshold
        self._model = None
        self._cooldown_until = 0.0
        self.available = False

    def load(self) -> bool:
        try:
            import os
            from pathlib import Path

            import openwakeword
            import openwakeword.utils
            from openwakeword.model import Model

            is_path = model_name_is_path(self.model_name)
            local = None
            if self.models_dir is not None and not is_path:
                oww = Path(self.models_dir) / "oww"
                cand = sorted(oww.glob(f"{self.model_name}*.onnx")) if oww.exists() else []
                if cand and (oww / "melspectrogram.onnx").exists() and (oww / "embedding_model.onnx").exists():
                    local = (str(cand[0]), str(oww / "melspectrogram.onnx"), str(oww / "embedding_model.onnx"))
            version = tuple(int(x) for x in getattr(openwakeword, "__version__", "0.6.0").split(".")[:2] if x.isdigit())
            if local:
                # downloaded by the app's model manager: nothing is written inside the app bundle
                try:  # openWakeWord >= 0.5
                    self._model = Model(wakeword_models=[local[0]], inference_framework="onnx",
                                        melspec_model_path=local[1], embedding_model_path=local[2])
                except TypeError:  # 0.4 uses its bundled feature models
                    self._model = Model(wakeword_model_paths=[local[0]])
            elif version >= (0, 5) or hasattr(openwakeword.utils, "download_models"):
                download = getattr(openwakeword.utils, "download_models", None)
                if download and not is_path:
                    try:
                        download(model_names=[self.model_name])
                    except TypeError:
                        download()
                self._model = Model(wakeword_models=[self.model_name], inference_framework="onnx")
            else:  # openWakeWord 0.4: models ship inside the package
                paths = [self.model_name] if is_path else [
                    p for p in openwakeword.get_pretrained_model_paths()
                    if os.path.basename(p).startswith(self.model_name)]
                if not paths:
                    raise FileNotFoundError(f"no bundled wake word model named {self.model_name!r}")
                self._model = Model(wakeword_model_paths=paths)
            self.available = True
            log.info("wake word '%s' ready", self.model_name)
        except Exception as exc:
            log.warning("openWakeWord unavailable (%s) — falling back to transcript wake detection", exc)
            self.available = False
        return self.available

    def process(self, frame: np.ndarray) -> bool:
        if self._model is None:
            return False
        scores = self._model.predict(frame)
        score = max(scores.values()) if scores else 0.0
        now = time.monotonic()
        if score >= self.threshold and now >= self._cooldown_until:
            self._cooldown_until = now + 1.5
            self._model.reset()
            if self.announce:
                bus.emit("wake", score=round(float(score), 3))
            return True
        return False

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset()


def model_name_is_path(name: str) -> bool:
    return name.endswith((".onnx", ".tflite")) or "/" in name


# ═══════════════════════════════ endpointing ═════════════════════════════════
class VAD:
    """Speech/non-speech per 20 ms sub-frame. webrtcvad if installed, else adaptive energy."""

    def __init__(self, aggressiveness: int = 2) -> None:
        self._vad = None
        try:
            import webrtcvad

            self._vad = webrtcvad.Vad(aggressiveness)
        except Exception:
            log.info("webrtcvad not installed — using energy VAD")
        self.noise_floor = 0.004

    def is_speech(self, frame: np.ndarray) -> bool:
        sub = 320  # 20 ms
        votes = 0
        chunks = [frame[i:i + sub] for i in range(0, len(frame) - sub + 1, sub)]
        for chunk in chunks:
            if self._vad is not None:
                if self._vad.is_speech(chunk.tobytes(), SAMPLE_RATE):
                    votes += 1
            else:
                rms = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32768.0) ** 2)))
                if rms > max(0.012, self.noise_floor * 3.0):
                    votes += 1
                else:
                    self.noise_floor = 0.98 * self.noise_floor + 0.02 * rms
        return votes >= max(1, len(chunks) // 2)


async def record_utterance(
    q: asyncio.Queue,
    vad: VAD,
    start_timeout: float = 6.0,
    end_silence: float = 0.8,
    max_seconds: float = 30.0,
    preroll: list[np.ndarray] | None = None,
    stop_event: asyncio.Event | None = None,
) -> Optional[np.ndarray]:
    """Collect frames until the speaker stops. Returns float32 audio or None if nobody spoke."""
    frames: list[np.ndarray] = list(preroll or [])
    ring: collections.deque = collections.deque(maxlen=6)  # ~0.5 s pre-roll
    started = False
    silence = 0.0
    t0 = time.monotonic()
    frame_s = FRAME / SAMPLE_RATE
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            frame = await asyncio.wait_for(q.get(), timeout=1.0)
        except asyncio.TimeoutError:
            if not started and time.monotonic() - t0 > start_timeout:
                return None
            continue
        speech = vad.is_speech(frame)
        if not started:
            ring.append(frame)
            if speech:
                started = True
                frames.extend(ring)
                bus.emit("state", state="listening", hearing=True)
            elif time.monotonic() - t0 > start_timeout:
                return None
            continue
        frames.append(frame)
        silence = 0.0 if speech else silence + frame_s
        if silence >= end_silence or len(frames) * frame_s >= max_seconds:
            break
    if not started or not frames:
        return None
    audio = np.concatenate(frames).astype(np.float32) / 32768.0
    return audio if len(audio) > SAMPLE_RATE * 0.3 else None


# ═══════════════════════════════ push to talk ════════════════════════════════
class PushToTalk:
    """Hold a key (default right Option) to talk. Needs Accessibility permission."""

    def __init__(self, key: str | None, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        self.key_name = key
        self.on_press = on_press
        self.on_release = on_release
        self._listener = None
        self.down = False

    def start(self) -> bool:
        if not self.key_name:
            return False
        import os
        import sys

        if sys.platform == "darwin" and os.environ.get("JARVIS_ALLOW_PYNPUT") != "1":
            # pynput's keyboard hook calls TSM APIs off the main thread and crashes (SIGTRAP) on
            # recent macOS. The app uses a native NSEvent monitor instead (jarvis/app/menubar.py).
            return False
        try:
            from pynput import keyboard
        except Exception:
            return False
        target = getattr(keyboard.Key, self.key_name, None) or keyboard.KeyCode.from_char(self.key_name)

        def press(key):
            if key == target and not self.down:
                self.down = True
                self.on_press()

        def release(key):
            if key == target and self.down:
                self.down = False
                self.on_release()

        try:
            self._listener = keyboard.Listener(on_press=press, on_release=release)
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as exc:  # pragma: no cover
            log.warning("push-to-talk unavailable: %s", exc)
            return False

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
