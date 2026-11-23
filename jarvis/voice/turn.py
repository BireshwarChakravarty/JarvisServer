"""Has the speaker finished? Smart Turn v3, a small open model that hears it in the voice itself.

A fixed silence timeout is a poor judge of a finished thought: "Set a timer for... ten minutes"
gets cut off at the pause, while a crisp "Yes." waits the full timeout before he answers. Smart
Turn (pipecat-ai, BSD-2, 8 MB int8 ONNX, Whisper-tiny encoder) listens to the last eight seconds
and says how likely it is that the turn is complete, from intonation and rhythm as well as words.

How it's used (jarvis.voice.audio.record_utterance): after a short pause it asks the model.
Complete → stop listening at once. Not complete → keep listening up to a longer patience window.
If the model isn't available the old fixed timeout applies, so nothing ever depends on it.

The audio features are Whisper's log-mel spectrogram, computed here in numpy so the app doesn't
need the transformers library; tests check it against the reference implementation.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger("jarvis.turn")

SAMPLE_RATE = 16000
SECONDS = 8
N_FFT, HOP, N_MELS = 400, 160, 80
MODEL_FILE = "smart-turn-v3.2-cpu.onnx"
MODEL_URL = "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/" + MODEL_FILE
MODEL_SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
MODEL_BYTES = 8_679_182


# ── Whisper features (matches transformers.WhisperFeatureExtractor) ─────────
def _hz_to_mel(f):
    f = np.asarray(f, dtype=np.float64)
    min_log_hz, min_log_mel, logstep = 1000.0, 15.0, 27.0 / np.log(6.4)
    mel = 3.0 * f / 200.0
    return np.where(f >= min_log_hz, min_log_mel + np.log(np.maximum(f, 1e-10) / min_log_hz) * logstep, mel)


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    min_log_hz, min_log_mel, logstep = 1000.0, 15.0, np.log(6.4) / 27.0
    hz = 200.0 * m / 3.0
    return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), hz)


def mel_filters(n_freqs: int = N_FFT // 2 + 1, n_mels: int = N_MELS, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Slaney-style triangular filters, slaney-normalised: shape (n_freqs, n_mels)."""
    fft_freqs = np.linspace(0, sr // 2, n_freqs)
    mel_pts = np.linspace(_hz_to_mel(0.0), _hz_to_mel(sr / 2), n_mels + 2)
    filt_freqs = _mel_to_hz(mel_pts)
    diff = np.diff(filt_freqs)
    slopes = filt_freqs[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / diff[:-1]
    up = slopes[:, 2:] / diff[1:]
    fb = np.maximum(0.0, np.minimum(down, up))
    enorm = 2.0 / (filt_freqs[2:n_mels + 2] - filt_freqs[:n_mels])
    return fb * enorm[None, :]


_FILTERS = None
_WINDOW = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)      # periodic Hann


def log_mel(audio: np.ndarray) -> np.ndarray:
    """(80, frames) log-mel spectrogram, Whisper's recipe."""
    global _FILTERS
    if _FILTERS is None:
        _FILTERS = mel_filters()
    x = np.pad(audio.astype(np.float64), N_FFT // 2, mode="reflect")
    n_frames = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n_frames)[:, None]
    frames = x[idx] * _WINDOW[None, :]
    power = np.abs(np.fft.rfft(frames, n=N_FFT, axis=1)) ** 2              # (frames, 201)
    mel = power @ _FILTERS                                                  # (frames, 80)
    logm = np.log10(np.maximum(mel, 1e-10)).T[:, :-1]
    logm = np.maximum(logm, logm.max() - 8.0)
    return ((logm + 4.0) / 4.0).astype(np.float32)


def features(audio: np.ndarray) -> np.ndarray:
    """The model's input: the last 8 s (zero-padded in front), normalised, as log-mel (1, 80, 800)."""
    n = SAMPLE_RATE * SECONDS
    a = np.asarray(audio, dtype=np.float32)
    a = a[-n:] if len(a) > n else np.pad(a, (n - len(a), 0))
    a = (a - a.mean()) / np.sqrt(a.var() + 1e-7)
    return log_mel(a)[None, :, :]


# ── the model ───────────────────────────────────────────────────────────────
class TurnDetector:
    def __init__(self, models_dir: Path | str | None) -> None:
        self.path = Path(models_dir) / "turn" / MODEL_FILE if models_dir else None
        self._sess = None
        self._lock = threading.Lock()
        self.available = False

    def load(self) -> bool:
        if self._sess is not None:
            return True
        if self.path is None or not self.path.exists():
            return False
        try:
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            so.inter_op_num_threads = 1
            so.intra_op_num_threads = 2
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._sess = ort.InferenceSession(str(self.path), sess_options=so, providers=["CPUExecutionProvider"])
            self.available = True
            log.info("Smart Turn end-of-turn model ready")
        except Exception as exc:
            log.warning("Smart Turn unavailable (%s): using the silence timeout", exc)
            self.available = False
        return self.available

    def probability(self, audio: np.ndarray) -> float | None:
        """P(the speaker has finished). None if the model can't answer."""
        if not self.load():
            return None
        try:
            with self._lock:
                out = self._sess.run(None, {"input_features": features(audio)})
            return float(np.asarray(out[0]).reshape(-1)[0])
        except Exception as exc:
            log.debug("Smart Turn inference failed: %s", exc)
            return None


# ── words that can't end a request ────────────────────────────────────────────────────────────────
# The voice can sound finished while the words plainly aren't: "remind me to… call the bank",
# "send it to Pepper and…", "um". When what you've said so far ends like this, JARVIS keeps
# listening for the rest instead of answering half a sentence.
DANGLING = frozenset("""
and or but so because cause to the a an of for with about from into onto at by than as whether
um umm uh uhh er erm hmm uhm like then which who whose whom if when while until unless after before
my your his her their our its this these those is are was were will would should could can
also plus and then maybe
""".split())


def sounds_unfinished(text: str) -> bool:
    """True when a transcript plainly stops mid-thought (a dangling word or a trailing comma/dash)."""
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith(("…", "...", ",", "—", "–", "-", ":", ";")):
        return True
    if t.endswith(("?", "!")):
        return False                    # a question or exclamation is a finished thought
    words = t.lower().rstrip(".!?\"'”’ ").split()
    if not words:
        return False
    last = words[-1].strip(",.;:!?\"'“”‘’()")
    if last in ("and", "or", "but", "um", "uh", "er", "erm", "uhm", "umm", "uhh"):
        return True
    # a lone short reply ("yes", "the first one") is complete even if it ends oddly
    return len(words) >= 3 and last in DANGLING
