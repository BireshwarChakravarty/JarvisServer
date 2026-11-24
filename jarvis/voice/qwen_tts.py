"""JARVIS's natural voice: Qwen3-TTS (Apache-2.0) on Apple Silicon, speaking in a voice designed for him.

The voices are original: each was made once with Qwen3-TTS VoiceDesign from a written description (a
refined English butler, a warm Irish woman…; see scripts/voice_lab.py) and is shipped as a ten-second
reference recording. At run time the Qwen3-TTS Base model (0.6B, 4-bit, about 1.7 GB, downloaded once)
speaks every reply in that voice, on this Mac, free and offline. The larger 1.7B model sounded a touch
richer but couldn't keep up with real-time speech on a Mac mini and more than doubled the download, so
JARVIS uses the lighter one only.

It's heavier than the Classic voice (Kokoro), so JARVIS starts with Classic, loads this in the
background, times it on this Mac and switches only if it keeps up with real-time speech. If it can't,
he stays on Classic and says so once, rather than ever stuttering.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("jarvis.qwen_tts")

_COMMON = {
    "config.json": 5_522, "generation_config.json": 245, "merges.txt": 1_671_839,
    "preprocessor_config.json": 127, "tokenizer_config.json": 7_344, "vocab.json": 2_776_833,
    "speech_tokenizer/config.json": 2_336, "speech_tokenizer/configuration.json": 76,
    "speech_tokenizer/model.safetensors": 682_293_092, "speech_tokenizer/preprocessor_config.json": 234,
}
# The model the natural voices run on (one size only, to keep JARVIS light).
TIERS = {
    "small": {"repo": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit", "dir": "qwen3-tts-0.6b-base-4bit",
              "group": "human", "files": {**_COMMON, "model.safetensors": 1_024_490_700,
                                         "model.safetensors.index.json": 77_731}},
}
STALE_DIRS = ["qwen3-tts-1.7b-base-4bit"]       # models earlier versions downloaded: removed if found

# The words every reference recording says (the model is given them alongside the audio).
REF_TEXT = ("Good evening, sir. I've taken the liberty of moving your three o'clock to tomorrow morning. "
            "The weather, I'm pleased to say, looks rather promising for the weekend.")

VOICES_DIR = Path(__file__).with_name("voices")
HUMAN_VOICES = {                # chosen by ear from the voice lab's candidates
    "butler": {"label": "Butler", "about": "Refined English butler: calm, dry, quietly witty"},
    "gentleman": {"label": "Gentleman", "about": "Deep, warm, unflappable English gentleman"},
    # FRIDAY-style: original Irish voices designed from descriptions (scripts/voice_lab.py, round 2).
    "friday": {"label": "Friday", "about": "Soft Irish accent: warm, quick, a light playful edge"},
    "friday_calm": {"label": "Friday Calm", "about": "Composed Irish voice: smooth, unflappable, dry humour"},
}
DEFAULT_VOICE = "butler"

MAX_RTF = 0.9                   # it must speak faster than this fraction of real time to be used


def tiers_for(voice: str) -> list[str]:
    """The models a voice may use, in the order to try them."""
    return list(HUMAN_VOICES.get(voice, {}).get("tiers", list(TIERS)))


def group_for(voice: str) -> str:
    """The download group of the model a voice tries first."""
    return TIERS[tiers_for(voice)[0]]["group"]


def model_dir(models_dir: Path, tier: str = "small") -> Path:
    return Path(models_dir) / TIERS[tier]["dir"]


def model_ready(models_dir: Path, tier: str = "small") -> bool:
    d = model_dir(models_dir, tier)
    return all((d / f).exists() for f in TIERS[tier]["files"])


def supported() -> bool:
    """Apple Silicon with MLX available."""
    import platform

    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False
    try:
        import mlx.core  # noqa: F401
        import mlx_audio  # noqa: F401
    except Exception:
        return False
    return True


class TooSlow(RuntimeError):
    pass


class QwenVoice:
    """A TTS backend (synth(text) -> float32 audio at 24 kHz). All MLX work happens on one thread
    that owns the model, as MLX requires."""

    name = "qwen3"
    sample_rate = 24000
    streams = True              # speaks whole passages, playing while it generates (see Speaker)
    STREAM_INTERVAL = 0.4       # seconds of speech per chunk: small enough to start at once

    def __init__(self, voice: str, models_dir: Path, max_rtf: float = MAX_RTF, tier: str = "small") -> None:
        if voice not in HUMAN_VOICES:
            raise ValueError(f"unknown voice {voice!r}")
        self.voice = voice
        self.tier = tier
        self.ref = VOICES_DIR / f"{voice}.wav"
        self.dir = model_dir(models_dir, tier)
        self.max_rtf = max_rtf
        self.rtf: float | None = None
        self._jobs: queue.Queue = queue.Queue()       # live speech: always first
        self._later: queue.Queue = queue.Queue()      # pre-rendering ("Yes, sir?"): only when idle
        self._thread: threading.Thread | None = None
        self.model = None

    # ── the model's own thread ─────────────────────────────────────────────
    def _next_job(self):
        try:
            return self._jobs.get_nowait()
        except queue.Empty:
            pass
        try:
            return self._later.get_nowait()
        except queue.Empty:
            pass
        return self._jobs.get(timeout=0.2) if self._later.empty() else None

    def _run(self) -> None:
        while True:
            try:
                job = self._next_job()
            except queue.Empty:
                continue
            if job is None:
                if self._closing:
                    return
                continue
            fn, box, done = job
            try:
                box["value"] = fn()
            except BaseException as exc:          # handed back to the caller
                box["error"] = exc
            done.set()

    _closing = False

    def _call(self, fn, timeout: float | None = 120, later: bool = False):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="qwen-tts")
            self._thread.start()
        box: dict = {}
        done = threading.Event()
        (self._later if later else self._jobs).put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("the voice took too long")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _chunks(self, text: str):
        """Streamed generation: the reference recording is encoded once (cached by the model) and never
        decoded again, unlike a non-streamed call, which decodes it with every sentence."""
        for r in self.model.generate(text=text, ref_audio=str(self.ref), ref_text=REF_TEXT, lang_code="english",
                                     stream=True, streaming_interval=self.STREAM_INTERVAL, split_pattern=""):
            yield np.asarray(r.audio, dtype=np.float32).reshape(-1)

    def _generate(self, text: str) -> np.ndarray:
        chunks = list(self._chunks(text))
        return np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)

    # ── public ─────────────────────────────────────────────────────────────
    def load(self, measure: bool = True) -> float:
        """Load, warm up and (unless already known) time it on this Mac: the median of three phrases,
        so one slow moment doesn't decide. Returns the real-time factor; raises TooSlow."""
        if not self.ref.exists():
            raise FileNotFoundError(self.ref)

        def work():
            from mlx_audio.tts.utils import load_model

            self.model = load_model(self.dir)
            self.sample_rate = int(getattr(self.model, "sample_rate", 24000) or 24000)
            self._generate("Good evening.")                   # first run compiles: not timed
            if not measure:
                return 0.0
            rtfs = []
            for line in ("Right. Two meetings this afternoon, the first at half past two, and it's drizzling.",
                         "Done. The lights are off, and the door's locked for the night.",
                         "Pepper's message says the board call moved to Thursday. Shall I draft a reply?"):
                t0 = time.perf_counter()
                audio = self._generate(line)
                rtfs.append((time.perf_counter() - t0) / max(len(audio) / self.sample_rate, 0.1))
            return sorted(rtfs)[1]
        self.rtf = float(self._call(work, timeout=600))
        if not measure:
            return self.rtf
        log.info("natural voice %s: %.2fx real time on this Mac", self.voice, self.rtf)
        if self.rtf > self.max_rtf:
            raise TooSlow(f"{self.rtf:.2f}x real time")
        return self.rtf

    def synth(self, text: str) -> np.ndarray:
        return self._call(lambda: self._generate(text))

    def synth_stream(self, text: str):
        """Yield the passage's audio chunk by chunk while it is generated (on the model's thread)."""
        out: queue.Queue = queue.Queue()
        end = object()
        failed: list = []
        abandon = threading.Event()        # the listener stopped (he was interrupted): stop generating

        def work():
            try:
                for c in self._chunks(text):
                    if abandon.is_set():
                        break
                    out.put(c)
            except BaseException as exc:          # handed back to the caller below
                failed.append(exc)
            finally:
                out.put(end)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="qwen-tts")
            self._thread.start()
        self._jobs.put((work, {}, threading.Event()))
        deadline = time.monotonic() + 120
        try:
            while True:
                try:
                    item = out.get(timeout=0.5)
                except queue.Empty:
                    if time.monotonic() > deadline:
                        raise TimeoutError("the voice took too long") from None
                    continue
                if item is end:
                    if failed:
                        raise failed[0]
                    return
                deadline = time.monotonic() + 120
                yield item
        finally:
            abandon.set()

    def synth_later(self, text: str) -> np.ndarray:
        """Pre-rendering: waits behind anything he's about to say live."""
        return self._call(lambda: self._generate(text), timeout=600, later=True)

    def close(self) -> None:
        self._closing = True
        self._jobs.put(None)
