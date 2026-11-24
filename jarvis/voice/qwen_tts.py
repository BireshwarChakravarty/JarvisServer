"""JARVIS's natural voice: Qwen3-TTS (Apache-2.0) on Apple Silicon, speaking in a voice designed for him.

The voices are original: each was made once with Qwen3-TTS VoiceDesign from a written description (a
refined English butler, an Oxford-educated man in his forties…; see scripts/voice_lab.py) and is shipped
as a ten-second reference recording. At run time the Qwen3-TTS Base model (1.7B, 4-bit, about 2.3 GB,
downloaded once; it ran no slower than the 0.6B model and sounded better) speaks every reply in that
voice, on this Mac, free and offline.

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

REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit"
DIRNAME = "qwen3-tts-1.7b-base-4bit"
FILES = {                       # what the model needs, with sizes (bytes) for download progress
    "config.json": 5_522, "generation_config.json": 245, "merges.txt": 1_671_839,
    "model.safetensors": 1_649_238_423, "model.safetensors.index.json": 78_070,
    "preprocessor_config.json": 127, "tokenizer_config.json": 7_344, "vocab.json": 2_776_833,
    "speech_tokenizer/config.json": 2_336, "speech_tokenizer/configuration.json": 76,
    "speech_tokenizer/model.safetensors": 682_293_092, "speech_tokenizer/preprocessor_config.json": 234,
}

# The words every reference recording says (the model is given them alongside the audio).
REF_TEXT = ("Good evening, sir. I've taken the liberty of moving your three o'clock to tomorrow morning. "
            "The weather, I'm pleased to say, looks rather promising for the weekend.")

VOICES_DIR = Path(__file__).with_name("voices")
HUMAN_VOICES = {                # chosen by ear from the voice lab's candidates
    "butler": {"label": "Butler", "about": "Refined English butler: calm, dry, quietly witty"},
    "assistant": {"label": "Assistant", "about": "Articulate and warm, with a hint of amusement"},
    "gentleman": {"label": "Gentleman", "about": "Deep, warm, unflappable English gentleman"},
}
DEFAULT_VOICE = "butler"

MAX_RTF = 0.9                   # it must speak faster than this fraction of real time to be used


def model_dir(models_dir: Path) -> Path:
    return Path(models_dir) / DIRNAME


def model_ready(models_dir: Path) -> bool:
    d = model_dir(models_dir)
    return all((d / f).exists() for f in FILES)


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

    def __init__(self, voice: str, models_dir: Path, max_rtf: float = MAX_RTF) -> None:
        if voice not in HUMAN_VOICES:
            raise ValueError(f"unknown voice {voice!r}")
        self.voice = voice
        self.ref = VOICES_DIR / f"{voice}.wav"
        self.dir = model_dir(models_dir)
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

    def _generate(self, text: str) -> np.ndarray:
        chunks = [np.asarray(r.audio, dtype=np.float32).reshape(-1)
                  for r in self.model.generate(text=text, ref_audio=str(self.ref), ref_text=REF_TEXT,
                                               lang_code="english")]
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
            for line in ("Right. Two meetings this afternoon, and it's drizzling.", "Done. The lights are off.",
                         "Pepper's message says the board call moved to Thursday."):
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

    def synth_later(self, text: str) -> np.ndarray:
        """Pre-rendering: waits behind anything he's about to say live."""
        return self._call(lambda: self._generate(text), timeout=600, later=True)

    def close(self) -> None:
        self._closing = True
        self._jobs.put(None)
