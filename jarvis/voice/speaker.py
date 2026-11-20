"""Voice ID: JARVIS learns your voice and answers only you (like Siri's "Hey Siri" voice setup).

How it works
  • A speaker-embedding model, CAM++ (3D-Speaker, Apache-2.0, trained on VoxCeleb English), turns a few
    seconds of speech into a 512-number voiceprint. Two clips from the same person point the same way;
    different people point elsewhere (cosine similarity).
  • You teach it once: five short phrases ("learn my voice"). Each becomes a voiceprint in your profile.
  • Each request then gets a score against your profile. Above the threshold it's you; clearly below
    it's someone else (a guest, the TV) and JARVIS stays quiet. Clips too short to judge are let through.
  • It keeps learning: requests that are confidently you are added to the profile now and then (a
    rolling set), so it follows your voice through colds, mornings and different microphones.

The model's front end is Kaldi's 80-bin log mel filterbank (25 ms frames, 10 ms hop, Povey window,
pre-emphasis 0.97), computed here in numpy and checked against kaldi-native-fbank in the tests, then
mean-normalised over time as the model expects. The profile lives in JARVIS's data folder, never
leaves the Mac, and "forget my voice" deletes it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("jarvis.speaker")

MODEL = "speaker/campplus_en_voxceleb.onnx"
MODEL_URL = ("https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main/"
             "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx")
MODEL_SIZE = 29_596_979
SR = 16000
MIN_SECONDS = 1.0            # shorter than this (of speech) is too little to judge: let it through
PROFILE_MAX = 40             # rolling set of voiceprints


# ── Kaldi fbank in numpy ──────────────────────────────────────────────────
def _mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


_BANKS: dict = {}


def mel_banks(num_bins: int = 80, n_fft: int = 512, sr: int = SR, low: float = 20.0, high: float = 0.0) -> np.ndarray:
    """Kaldi's triangular mel filters, (num_bins, n_fft // 2 + 1); the Nyquist column is zero as in Kaldi."""
    key = (num_bins, n_fft, sr, low, high)
    if key in _BANKS:
        return _BANKS[key]
    high = high if high > 0 else sr / 2 + high
    half = n_fft // 2
    bin_w = sr / n_fft
    mlo, mhi = _mel(low), _mel(high)
    delta = (mhi - mlo) / (num_bins + 1)
    mels = _mel(bin_w * np.arange(half))
    banks = np.zeros((num_bins, half + 1), dtype=np.float64)
    for b in range(num_bins):
        left, center, right = mlo + b * delta, mlo + (b + 1) * delta, mlo + (b + 2) * delta
        up = (mels - left) / (center - left)
        down = (right - mels) / (right - center)
        w = np.where((mels > left) & (mels < right), np.where(mels <= center, up, down), 0.0)
        banks[b, :half] = w
    _BANKS[key] = banks
    return banks


def fbank(audio: np.ndarray, num_bins: int = 80) -> np.ndarray:
    """Kaldi-compatible log mel filterbank of 16 kHz float audio in [-1, 1] → (frames, num_bins)."""
    x = np.asarray(audio, dtype=np.float64).reshape(-1)
    flen, shift, n_fft = 400, 160, 512
    if len(x) < flen:
        return np.zeros((0, num_bins), np.float32)
    n = 1 + (len(x) - flen) // shift                       # snip_edges
    idx = np.arange(flen)[None, :] + shift * np.arange(n)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)    # remove DC offset
    pre = np.concatenate([frames[:, :1] * 0.97, frames[:, :-1] * 0.97], axis=1)
    frames = frames - pre                                   # pre-emphasis (first sample against itself)
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(flen) / (flen - 1))) ** 0.85   # Povey
    spec = np.abs(np.fft.rfft(frames * win, n=n_fft)) ** 2
    energies = spec @ mel_banks(num_bins, n_fft).T
    return np.log(np.maximum(energies, np.finfo(np.float32).eps)).astype(np.float32)


# ── the model ─────────────────────────────────────────────────────────────
class SpeakerModel:
    def __init__(self, models_dir: Path) -> None:
        self.path = Path(models_dir) / MODEL
        self.session = None
        self.available = False
        self._lock = threading.Lock()

    def load(self) -> bool:
        if not self.path.exists():
            log.info("voice ID model not downloaded yet")
            return False
        try:
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.intra_op_num_threads = 2
            self.session = ort.InferenceSession(str(self.path), so, providers=["CPUExecutionProvider"])
            self.available = True
        except Exception as exc:
            log.warning("voice ID model unavailable: %s", exc)
            self.available = False
        return self.available

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        """A unit-length voiceprint, or None if there's too little speech."""
        if not self.available:
            return None
        feats = fbank(speech_only(audio))
        if len(feats) < 100:                                  # < ~1 s
            return None
        feats = feats - feats.mean(axis=0, keepdims=True)     # global mean normalisation
        with self._lock:
            out = self.session.run(None, {self.session.get_inputs()[0].name: feats[None].astype(np.float32)})[0]
        v = np.asarray(out, dtype=np.float32).reshape(-1)
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else None


def speech_only(audio: np.ndarray, frame: int = 480) -> np.ndarray:
    """Drop the silent stretches (30 ms frames well below the clip's loud part): a voiceprint should
    describe your voice, not the pauses or the room between words."""
    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    n = len(a) // frame
    if n < 4:
        return a
    f = a[:n * frame].reshape(n, frame)
    rms = np.sqrt(np.mean(f * f, axis=1))
    keep = rms >= max(1e-4, 0.12 * float(np.percentile(rms, 90)))
    return f[keep].reshape(-1) if keep.sum() >= 4 else a


# ── your profile ──────────────────────────────────────────────────────────
class VoiceProfile:
    """Your enrolled voiceprints (the first five from 'learn my voice', then a rolling set)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.prints: list[np.ndarray] = []
        self.enrolled = 0
        self.updated = 0.0
        self.load()

    @property
    def ready(self) -> bool:
        return len(self.prints) >= 3

    def load(self) -> None:
        try:
            d = json.loads(self.path.read_text())
            self.prints = [np.asarray(p, np.float32) for p in d.get("prints", [])]
            self.enrolled = int(d.get("enrolled", len(self.prints)))
            self.updated = float(d.get("updated", 0.0))
        except (OSError, ValueError, TypeError):
            self.prints, self.enrolled = [], 0

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"prints": [p.round(5).tolist() for p in self.prints], "enrolled": self.enrolled,
                                   "updated": self.updated}))
        tmp.replace(self.path)

    def clear(self) -> None:
        self.prints, self.enrolled = [], 0
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def enrol(self, prints: list[np.ndarray]) -> None:
        self.prints = [p for p in prints if p is not None]
        self.enrolled = len(self.prints)
        self.updated = time.time()
        self.save()

    def score(self, v: np.ndarray) -> float:
        """Similarity to you: the mean of the best three matches (robust to one odd enrolment)."""
        if not self.prints or v is None:
            return 0.0
        sims = np.sort(np.stack(self.prints) @ v)[::-1]
        return float(np.mean(sims[:3]))

    def learn(self, v: np.ndarray, min_gap: float = 300.0) -> bool:
        """Keep a confidently-you voiceprint (at most every few minutes), oldest learned ones out first;
        the five you enrolled with are always kept."""
        now = time.time()
        if v is None or now - self.updated < min_gap:
            return False
        self.prints.append(np.asarray(v, np.float32))
        if len(self.prints) > PROFILE_MAX:
            del self.prints[self.enrolled]
        self.updated = now
        self.save()
        return True


class VoiceID:
    """Model + profile + the decision.

    Verdicts: 'you' · 'not_you' · 'unsure' (a voice in between) · 'short' (too little speech to
    judge, e.g. "yes") · 'off' (not set up). Measured on LibriSpeech test-clean (40 speakers, three
    enrolment clips each): at 0.45, 1.5% of your requests rejected, 0.16% of other people accepted."""

    def __init__(self, models_dir: Path, profile_path: Path, threshold: float = 0.45, margin: float = 0.12) -> None:
        self.model = SpeakerModel(models_dir)
        self.profile = VoiceProfile(profile_path)
        self.threshold = threshold      # at or above: you
        self.margin = margin            # below threshold - margin: someone else; between: unsure

    def load(self) -> bool:
        return self.model.load()

    @property
    def active(self) -> bool:
        return self.model.available and self.profile.ready

    def check(self, audio: np.ndarray, learn: bool = True) -> tuple[str, float]:
        if not self.active:
            return "off", 0.0
        v = self.model.embed(audio)
        if v is None:
            return "short", 0.0
        s = self.profile.score(v)
        if s >= self.threshold:
            if learn and s >= self.threshold + 0.15:
                self.profile.learn(v)
            return "you", s
        if s < self.threshold - self.margin:
            return "not_you", s
        return "unsure", s
