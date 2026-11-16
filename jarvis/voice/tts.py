"""Text-to-speech with a pipelined, interruptible speaker.

Backends, best first:
  kokoro-mlx   Kokoro-82M on Apple Silicon GPU via mlx-audio (voice: bm_george — British male)
  kokoro-onnx  Kokoro-82M on CPU via onnxruntime (any Mac)
  say          macOS built-in voices (Daniel), zero install

Sentence N+1 is synthesised while sentence N plays, so long answers flow without gaps,
and stop() silences JARVIS instantly when you interrupt him.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

from jarvis.events import bus
from jarvis.voice.text import speech_form

log = logging.getLogger("jarvis.tts")

KOKORO_ONNX_URLS = {
    "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}


# Kokoro voice presets. A spec is one voice ("bm_george") or a weighted blend of style vectors
# ("bm_george:0.55,bm_fable:0.45"); blending averages the voices' timbre and delivery.
VOICE_PRESETS = {
    "jarvis": {"spec": "bm_george:0.5,bm_fable:0.5", "speed": 0.94,
               "label": "JARVIS", "about": "Calm, dry British butler (a blend of George and Fable)"},
    "george": {"spec": "bm_george", "speed": 1.0, "label": "George", "about": "Warm, steady British male"},
    "fable": {"spec": "bm_fable", "speed": 1.0, "label": "Fable", "about": "Brighter, more expressive British male"},
    "lewis": {"spec": "bm_lewis", "speed": 1.0, "label": "Lewis", "about": "Deeper British male"},
    "emma": {"spec": "bf_emma", "speed": 1.0, "label": "Emma", "about": "British female (a FRIDAY alternative)"},
}


def parse_voice_spec(spec: str) -> list[tuple[str, float]]:
    """'bm_george:0.55,bm_fable:0.45' -> [('bm_george', .55), ('bm_fable', .45)] (weights normalised)."""
    spec = VOICE_PRESETS.get(spec, {}).get("spec", spec)
    parts = []
    for item in spec.replace("+", ",").split(","):
        item = item.strip()
        if not item:
            continue
        name, _, w = item.partition(":")
        parts.append((name.strip(), float(w) if w else 1.0))
    total = sum(w for _, w in parts) or 1.0
    return [(n, w / total) for n, w in parts]


class TTSBackend:
    name = "base"
    sample_rate = 24000

    def synth(self, text: str) -> np.ndarray:  # float32 mono in [-1, 1]
        raise NotImplementedError


class KokoroMLX(TTSBackend):
    name = "kokoro-mlx"

    def __init__(self, voice: str, speed: float) -> None:
        from mlx_audio.tts.utils import load_model

        self.voice, self.speed = parse_voice_spec(voice)[0][0], speed   # MLX takes one voice, not a blend
        self.lang = "b" if self.voice.startswith("b") else "a"
        self.model = load_model("mlx-community/Kokoro-82M-bf16")

    def synth(self, text: str) -> np.ndarray:
        chunks = []
        try:
            results = self.model.generate(text=text, voice=self.voice, speed=self.speed, lang_code=self.lang)
        except TypeError:
            results = self.model.generate(text=text, voice=self.voice, speed=self.speed)
        for r in results:
            chunks.append(np.asarray(r.audio, dtype=np.float32).reshape(-1))
            sr = getattr(r, "sample_rate", None)
            if sr:
                self.sample_rate = int(sr)
        return np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)


class KokoroONNX(TTSBackend):
    name = "kokoro-onnx"

    def __init__(self, voice: str, speed: float, models_dir: Path) -> None:
        from kokoro_onnx import Kokoro

        paths = {}
        for fname, url in KOKORO_ONNX_URLS.items():
            path = models_dir / fname
            if not path.exists():
                log.info("downloading %s …", fname)
                _download(url, path)
            paths[fname] = path
        self.kokoro = Kokoro(str(paths["kokoro-v1.0.onnx"]), str(paths["voices-v1.0.bin"]))
        self.speed = speed
        self.set_voice(voice)

    def set_voice(self, voice: str) -> None:
        parts = parse_voice_spec(voice)
        missing = [n for n, _ in parts if n not in self.kokoro.voices]
        if missing or not parts:
            log.warning("unknown Kokoro voice %s; using bm_george", missing or voice)
            parts = [("bm_george", 1.0)]
        self.voice = voice
        self.style = sum(self.kokoro.get_voice_style(n) * w for n, w in parts).astype(np.float32)
        self.lang = "en-gb" if parts[0][0].startswith("b") else "en-us"

    def synth(self, text: str) -> np.ndarray:
        samples, sr = self.kokoro.create(text, voice=self.style, speed=self.speed, lang=self.lang)
        self.sample_rate = int(sr)
        return np.asarray(samples, dtype=np.float32)


class MacSay(TTSBackend):
    name = "say"
    sample_rate = 22050

    def __init__(self, voice: str, speed: float) -> None:
        if not shutil.which("say"):
            raise RuntimeError("`say` not found")
        self.voice = best_mac_voice() if voice in (None, "", "auto") else voice
        self.rate = int(180 * speed)

    def synth(self, text: str) -> np.ndarray:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            cmd = ["say", "-r", str(self.rate), "-o", path, "--file-format=WAVE",
                   f"--data-format=LEI16@{self.sample_rate}", text]
            if self.voice:
                cmd[1:1] = ["-v", self.voice]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0 and self.voice:  # voice not installed → system default
                cmd = [c for c in cmd if c not in ("-v", self.voice)]
                subprocess.run(cmd, check=True, capture_output=True)
            with wave.open(path, "rb") as wf:
                raw = wf.readframes(wf.getnframes())
                self.sample_rate = wf.getframerate()
            return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        finally:
            Path(path).unlink(missing_ok=True)


# Apple's downloadable neural voices sound far better than the compact defaults.
# Best British male first. (System Settings > Accessibility > Spoken Content > Manage Voices.)
MAC_VOICE_PREFERENCE = ["Jamie (Premium)", "Jamie (Enhanced)", "Daniel (Premium)", "Daniel (Enhanced)",
                        "Oliver (Premium)", "Oliver (Enhanced)", "Arthur (Premium)", "Arthur (Enhanced)",
                        "Jamie", "Daniel", "Oliver", "Arthur"]


def mac_voices() -> list[dict]:
    """Installed `say` voices: [{'name', 'lang'}]."""
    if not shutil.which("say"):
        return []
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    import re
    voices = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s{2,}([a-z]{2}[_-][A-Z]{2})\s+#", line)
        if m:
            voices.append({"name": m.group(1).strip(), "lang": m.group(2).replace("-", "_")})
    return voices


def best_mac_voice(installed: list[dict] | None = None) -> str:
    names = {v["name"] for v in (installed if installed is not None else mac_voices())}
    return next((v for v in MAC_VOICE_PREFERENCE if v in names), "Daniel")


class SilentTTS(TTSBackend):
    """Used in tests / headless mode: no audio, just events."""
    name = "silent"

    def synth(self, text: str) -> np.ndarray:
        return np.zeros(int(self.sample_rate * min(0.05 * len(text.split()), 0.2)), dtype=np.float32)


def _download(urls: str | list[str], dest: Path) -> None:
    """Download to dest atomically, trying each mirror in turn."""
    import httpx

    last: Exception | None = None
    for url in [urls] if isinstance(urls, str) else urls:
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
            tmp.rename(dest)
            return
        except Exception as exc:
            last = exc
            tmp.unlink(missing_ok=True)
            log.warning("download failed from %s: %s", url, exc)
    raise RuntimeError(f"couldn't download {dest.name}: {last}")


def make_backend(kind: str, voice: str, speed: float, say_voice: str, models_dir: Path) -> TTSBackend:
    if kind == "silent":
        return SilentTTS()
    voice = voice or "jarvis"
    speed = float(speed or 1.0) * VOICE_PRESETS.get(voice, {}).get("speed", 1.0)   # presets carry their own pace
    order = [kind] if kind != "auto" else ["kokoro-onnx", "say"]
    if kind == "auto" and not (models_dir / "kokoro-v1.0.onnx").exists():
        order = ["say", "kokoro-onnx"]  # don't block on a 300 MB download; setup fetches it
    for name in order:
        try:
            if name == "kokoro-mlx":
                return KokoroMLX(voice, speed)
            if name == "kokoro-onnx":
                return KokoroONNX(voice, speed, models_dir)
            if name == "say":
                return MacSay(say_voice, speed)
        except Exception as exc:
            log.warning("TTS backend %s unavailable: %s", name, exc)
    log.warning("No TTS backend available — running silent")
    return SilentTTS()


def breath_after(text: str) -> float:
    """The pause a person leaves after a phrase: longer after a full stop than after a comma."""
    t = text.rstrip().rstrip("\"')]")
    if t.endswith("?"):
        return 0.32
    if t.endswith((".", "!", "…")):
        return 0.26
    if t.endswith((",", ";", ":")):
        return 0.12
    return 0.08


def shape_audio(audio: np.ndarray, sr: int, pause: float) -> np.ndarray:
    """Clip-safe, 6 ms fades so chunk joins never click, then the breath that follows the phrase."""
    a = np.clip(np.asarray(audio, dtype=np.float32), -1, 1).copy()
    f = min(int(sr * 0.006), len(a) // 2)
    if f > 0:
        ramp = np.linspace(0, 1, f, dtype=np.float32)
        a[:f] *= ramp
        a[-f:] *= ramp[::-1]
    return np.concatenate([a, np.zeros(int(sr * pause), dtype=np.float32)])


# ═══════════════════════════════ the speaker ═════════════════════════════════
class Speaker:
    def __init__(self, backend: TTSBackend, output_device: str | None = None) -> None:
        self.backend = backend
        self.output_device = output_device
        self._text_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue(maxsize=3)
        self._gen = 0  # bumping this invalidates everything queued
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._stop_play = threading.Event()
        self._playing = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sd = None
        try:
            import sounddevice as sd

            self._sd = sd
        except Exception:
            log.info("sounddevice not available — audio playback disabled")

    def start(self) -> None:
        for target in (self._synth_worker, self._play_worker):
            t = threading.Thread(target=target, daemon=True, name=target.__name__)
            t.start()
            self._threads.append(t)

    # ── public ───────────────────────────────────────────────────────────
    @property
    def speaking(self) -> bool:
        return not self._idle.is_set()

    current_text = ""
    # Where his voice goes. None: this Mac's speakers. Otherwise a callable(audio, sample_rate, text)
    # that sends it somewhere else (the iPhone you spoke from) and returns False if it couldn't.
    route = None

    def saying(self, word: str) -> bool:
        """Is the phrase playing right now saying `word`? (Used to ignore his own voice.)"""
        return word.lower() in (self.current_text or "").lower()

    def enqueue(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._pending_lock:
            self._pending += 1
            self._idle.clear()
        self._text_q.put((self._gen, text))

    async def say(self, text: str) -> None:
        self.enqueue(text)

    async def wait_done(self, timeout: float | None = None) -> None:
        await asyncio.to_thread(self._idle.wait, timeout)

    def stop(self) -> None:
        """Interrupt immediately and drop anything queued."""
        self._gen += 1
        self._stop_play.set()
        for q in (self._text_q, self._audio_q):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        with self._pending_lock:
            self._pending = 0
            self._idle.set()
        bus.emit("level", source="tts", value=0.0)

    def close(self) -> None:
        """Stop and let both worker threads exit (a replaced speaker mustn't linger)."""
        self.stop()
        self._closed = True
        self._text_q.put((None, None))
        self._audio_q.put((None, None, None, None, None))

    def play_tone(self, kind: str = "wake") -> None:
        """Short UI chimes, synthesised on the fly."""
        if self._sd is None:
            return
        sr = 24000
        specs = {
            "wake": [(880, 0.07), (1320, 0.09)],
            "done": [(1320, 0.06), (990, 0.08)],
            "error": [(440, 0.12), (330, 0.16)],
            "boot": [(523, 0.08), (659, 0.08), (784, 0.08), (1047, 0.16)],
        }
        parts = []
        for freq, dur in specs.get(kind, specs["wake"]):
            t = np.linspace(0, dur, int(sr * dur), endpoint=False)
            env = np.minimum(1, np.minimum(t / 0.01, (dur - t) / 0.03))
            parts.append(0.18 * np.sin(2 * np.pi * freq * t) * env)
        tone = np.concatenate(parts).astype(np.float32)
        try:
            self._sd.play(tone, sr, device=self._device_index())
        except Exception:
            pass

    # ── workers ──────────────────────────────────────────────────────────
    def _done_one(self) -> None:
        with self._pending_lock:
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                self._idle.set()
                bus.emit("level", source="tts", value=0.0)

    # Kokoro's prosody is best on phrase-sized input (~100-200 tokens): one sentence at a time
    # restarts the intonation every few words. The first sentence still goes alone so JARVIS starts
    # talking at once; whatever has queued up behind it while it was being voiced is merged.
    PHRASE_CHARS = 220

    def _take_phrase(self, gen: int, text: str) -> tuple[str, int]:
        parts, n = [text], 1
        while sum(len(p) for p in parts) < self.PHRASE_CHARS:
            try:
                g, nxt = self._text_q.get_nowait()
            except queue.Empty:
                break
            if g != gen:
                continue  # stale (interrupted); _gen moved on
            if sum(len(p) for p in parts) + len(nxt) > self.PHRASE_CHARS * 1.4:
                self._text_q.queue.appendleft((g, nxt))  # too long to merge: keep it for next time
                break
            parts.append(nxt)
            n += 1
        return " ".join(parts), n

    def _synth_worker(self) -> None:
        while True:
            gen, text = self._text_q.get()
            if getattr(self, "_closed", False):
                return
            if gen != self._gen:
                continue
            count = 1
            if self._playing.is_set() or not self._audio_q.empty():   # merging can't delay the start
                text, count = self._take_phrase(gen, text)
            try:
                t0 = time.perf_counter()
                audio = self.backend.synth(speech_form(text))
                log.debug("synth %.0f ms for %d chars", (time.perf_counter() - t0) * 1000, len(text))
            except Exception as exc:
                log.error("TTS failed: %s", exc)
                for _ in range(count):
                    self._done_one()
                continue
            if gen != self._gen:
                continue
            self._audio_q.put((gen, text, audio, self.backend.sample_rate, count))

    def _device_index(self):
        if not self.output_device or self._sd is None:
            return None
        from jarvis.voice.audio import _find_device

        return _find_device(self.output_device, "output")

    def _play_worker(self) -> None:
        while True:
            gen, text, audio, sr, count = self._audio_q.get()
            if getattr(self, "_closed", False):
                return
            if gen != self._gen:
                continue
            self._stop_play.clear()
            self._playing.set()
            self.current_text = text
            # duration lets the HUD pace subtitles to the actual audio, word for word
            bus.emit("speaking", text=text, duration=round(len(audio) / max(1, sr), 2))
            try:
                sink = self.route
                if sink is not None and self._send_elsewhere(sink, audio, sr, text, gen):
                    continue
                self._play(audio, sr, gen, pause=breath_after(text))
            except Exception as exc:  # no output device, device unplugged … keep the worker alive
                log.error("audio playback failed: %s", exc)
                bus.emit("error", message=f"Audio output problem: {exc}")
            finally:
                self._playing.clear()
                self.current_text = ""
                for _ in range(count):
                    self._done_one()

    def _send_elsewhere(self, sink, audio: np.ndarray, sr: int, text: str, gen: int) -> bool:
        """Hand the phrase to another device, then keep time as if it were playing here, so
        'speaking', subtitles, barge-in and the reactor behave exactly the same."""
        shaped = shape_audio(audio, sr, breath_after(text))
        try:
            if not sink(shaped, sr, text):
                return False
        except Exception as exc:
            log.warning("couldn't send speech to the other device: %s", exc)
            return False
        block = int(sr * 0.04)
        start = time.monotonic()
        for n, i in enumerate(range(0, len(shaped), block)):
            if self._stop_play.is_set() or gen != self._gen:
                break
            chunk = shaped[i:i + block]
            bus.emit("level", source="tts", value=float(min(1.0, np.sqrt(np.mean(chunk ** 2)) * 6)) if len(chunk) else 0.0)
            wait = start + (n + 1) * 0.04 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        return True

    def _play(self, audio: np.ndarray, sr: int, gen: int, pause: float = 0.08) -> None:
        block = int(sr * 0.04)
        if self._sd is None:
            # headless: emulate playback time so the state machine behaves the same
            for i in range(0, len(audio), block):
                if self._stop_play.is_set() or gen != self._gen:
                    return
                chunk = audio[i:i + block]
                bus.emit("level", source="tts", value=float(min(1.0, np.sqrt(np.mean(chunk ** 2)) * 6)) if len(chunk) else 0.0)
            return
        audio = shape_audio(audio, sr, pause)
        with self._sd.OutputStream(samplerate=sr, channels=1, dtype="float32",
                                   device=self._device_index(), blocksize=block) as stream:
            for i in range(0, len(audio), block):
                if self._stop_play.is_set() or gen != self._gen:
                    break
                chunk = audio[i:i + block]
                rms = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
                bus.emit("level", source="tts", value=min(1.0, rms * 6))
                stream.write(chunk.reshape(-1, 1))
