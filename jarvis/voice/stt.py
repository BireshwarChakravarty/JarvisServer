"""Speech-to-text. Apple Silicon: mlx-whisper on the GPU (Metal). Otherwise faster-whisper on CPU."""
from __future__ import annotations

import logging
import platform
import re
import threading
import time

import numpy as np

log = logging.getLogger("jarvis.stt")

MLX_REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium.en": "mlx-community/whisper-medium.en-mlx",
    "small.en": "mlx-community/whisper-small.en-mlx",
    "base.en": "mlx-community/whisper-base.en-mlx",
    "tiny.en": "mlx-community/whisper-tiny.en-mlx",
}

# Whisper's favourite hallucinations on silence / noise
HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "thank you for watching.", "you", "bye.", ".",
    "thanks for watching.", "subtitles by the amara.org community", "",
}


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


class Transcriber:
    def __init__(self, backend: str = "auto", model: str = "large-v3-turbo", language: str = "en",
                 vocabulary: list[str] | None = None) -> None:
        self.backend = backend
        self.model_name = model
        self.language = language or None
        self.prompt = ", ".join(["Jarvis"] + (vocabulary or [])) + "."
        self._impl = None
        self._lock = threading.Lock()
        self.name = "none"

    def load(self) -> str:
        with self._lock:
            if self._impl is not None:
                return self.name
            order = [self.backend] if self.backend != "auto" else (
                ["mlx", "faster-whisper"] if is_apple_silicon() else ["faster-whisper"]
            )
            for name in order:
                try:
                    if name == "mlx":
                        import mlx_whisper  # noqa: F401

                        repo = MLX_REPOS.get(self.model_name, self.model_name)
                        self._impl = ("mlx", repo)
                    elif name == "faster-whisper":
                        from faster_whisper import WhisperModel

                        size = self.model_name
                        if not is_apple_silicon() and size.startswith("large"):
                            size = "small.en" if (self.language or "en") == "en" else "small"
                        self._impl = ("fw", WhisperModel(size, device="auto", compute_type="int8"))
                    self.name = name
                    break
                except Exception as exc:
                    log.warning("STT backend %s unavailable: %s", name, exc)
            if self._impl is None:
                raise RuntimeError("No speech-to-text backend available. pip install 'jarvis-mac[voice,apple]'")
            log.info("STT backend: %s", self.name)
            return self.name

    def warmup(self) -> None:
        try:
            self.transcribe(np.zeros(16000, dtype=np.float32))
        except Exception as exc:  # pragma: no cover
            log.warning("STT warmup failed: %s", exc)

    def transcribe(self, audio: np.ndarray) -> str:
        self.load()
        t0 = time.perf_counter()
        kind, impl = self._impl  # type: ignore[misc]
        if kind == "mlx":
            import mlx_whisper

            result = mlx_whisper.transcribe(
                audio, path_or_hf_repo=impl, language=self.language,
                initial_prompt=self.prompt, condition_on_previous_text=False, fp16=True,
            )
            text = result.get("text", "")
        else:
            segments, _ = impl.transcribe(
                audio, language=self.language, beam_size=1, vad_filter=True,
                initial_prompt=self.prompt, condition_on_previous_text=False,
            )
            text = " ".join(s.text for s in segments)
        text = clean_transcript(text)
        log.info("STT %.0f ms: %r", (time.perf_counter() - t0) * 1000, text)
        return text


def clean_transcript(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if text.lower() in HALLUCINATIONS:
        return ""
    return text


WAKE_PATTERN = re.compile(r"\b(hey|hi|ok|okay|yo)?[\s,]*(jarvis|jarvi|javis|jervis|jarvas)\b[\s,.!?]*", re.I)


def strip_wake_phrase(text: str) -> tuple[bool, str]:
    """('hey jarvis, what time is it') -> (True, 'what time is it')"""
    m = WAKE_PATTERN.search(text)
    if not m:
        return False, text
    rest = (text[:m.start()] + " " + text[m.end():]).strip(" ,.")
    return True, rest
