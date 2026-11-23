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
    def __init__(self, backend: str = "auto", model: str = "small.en", language: str = "en",
                 vocabulary: list[str] | None = None, models_dir=None) -> None:
        self.models_dir = models_dir
        self.backend = backend
        self.model_name = model
        self.language = language or None
        self.prompt = ", ".join(["Jarvis"] + (vocabulary or [])) + "."
        self._impl = None
        self._lock = threading.Lock()
        self._busy = threading.Lock()   # one transcription at a time: the Mac's mic and a phone can overlap
        self.name = "none"

    def load(self) -> str:
        with self._lock:
            if self._impl is not None:
                return self.name
            local = self._local_model()
            order = [self.backend] if self.backend != "auto" else (
                ["faster-whisper"] if local else (["mlx", "faster-whisper"] if is_apple_silicon() else ["faster-whisper"])
            )
            for name in order:
                try:
                    if name == "mlx":
                        import mlx_whisper  # noqa: F401

                        repo = MLX_REPOS.get(self.model_name, self.model_name)
                        self._impl = ("mlx", repo)
                    elif name == "faster-whisper":
                        from faster_whisper import WhisperModel

                        size = local or self.model_name
                        if not local and not is_apple_silicon() and size.startswith("large"):
                            size = "small.en" if (self.language or "en") == "en" else "small"
                        import os

                        threads = max(2, min(8, (os.cpu_count() or 4) - 2))
                        self._impl = ("fw", WhisperModel(size, device="cpu", compute_type="int8",
                                                         cpu_threads=threads))
                    self.name = name
                    break
                except Exception as exc:
                    log.warning("STT backend %s unavailable: %s", name, exc)
            if self._impl is None:
                raise RuntimeError("No speech-to-text backend available. pip install 'jarvis-mac[voice,apple]'")
            log.info("STT backend: %s", self.name)
            return self.name

    def _local_model(self) -> str | None:
        if self.models_dir is None:
            return None
        from pathlib import Path

        d = Path(self.models_dir) / f"whisper-{self.model_name}"
        return str(d) if (d / "model.bin").exists() else None

    def warmup(self) -> None:
        try:
            self.transcribe(np.zeros(16000, dtype=np.float32))
        except Exception as exc:  # pragma: no cover
            log.warning("STT warmup failed: %s", exc)

    last_confidence: float | None = None    # of the latest transcript (see _confidence)

    def transcribe(self, audio: np.ndarray) -> str:
        self.load()
        with self._busy:
            return self._transcribe(audio)

    def _transcribe(self, audio: np.ndarray) -> str:
        t0 = time.perf_counter()
        kind, impl = self._impl  # type: ignore[misc]
        if kind == "mlx":
            import mlx_whisper

            result = mlx_whisper.transcribe(
                audio, path_or_hf_repo=impl, language=self.language,
                initial_prompt=self.prompt, condition_on_previous_text=False, fp16=True,
            )
            segs = result.get("segments")
            kept = [sg for sg in segs or [] if _real(sg.get("no_speech_prob"), sg.get("avg_logprob"))]
            text = " ".join(str(sg.get("text", "")) for sg in kept) if segs else result.get("text", "")
            self.last_confidence = _confidence([sg.get("avg_logprob") for sg in kept])
        else:
            segments, _ = impl.transcribe(
                audio, language=self.language, beam_size=1,
                # the audio is already trimmed to your speech: skip the extra VAD pass on short turns
                vad_filter=len(audio) > 16000 * 20, without_timestamps=True,
                initial_prompt=self.prompt, condition_on_previous_text=False,
            )
            kept = [s for s in segments if _real(getattr(s, "no_speech_prob", None), getattr(s, "avg_logprob", None))]
            text = " ".join(s.text for s in kept)
            self.last_confidence = _confidence([getattr(s, "avg_logprob", None) for s in kept])
        text = clean_transcript(text)
        log.info("STT %.0f ms: %r", (time.perf_counter() - t0) * 1000, text)
        return text


def _confidence(logprobs: list) -> float | None:
    """How sure the recogniser was (its average per-token log-probability; below about -0.8 it was
    guessing). None when it didn't say."""
    vals = [float(v) for v in logprobs if v is not None]
    return sum(vals) / len(vals) if vals else None


def _real(no_speech_prob, avg_logprob) -> bool:
    """Whisper's own test for 'there was no speech here, the words are invented' (as in its
    reference decoder): likely silence and low confidence. Noise, a fan, a door, a distant TV."""
    try:
        return not (float(no_speech_prob) > 0.6 and float(avg_logprob) < -1.0)
    except (TypeError, ValueError):
        return True


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
