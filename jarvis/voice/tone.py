"""How you sound: JARVIS reads your tone of voice the way a person does.

Not your words (the brain has those) but how they were said: how fast, how loud, how high and how
lively your voice was, compared with how you usually sound. So "you sound hurried" means hurried for
you, not for anyone. The result is a few plain words ("in a hurry", "quiet, perhaps tired") that go
to the brain with the request, so the manner of the reply fits; JARVIS mentions it aloud only rarely,
as a person would ("Long day?").

Everything stays on this Mac: a handful of numbers per request, and a running picture of your usual
voice kept in his memory.
"""
from __future__ import annotations

import math

import numpy as np

SR = 16000
FRAME = 640                  # 40 ms
FEATURES = ("rate", "loudness", "pitch", "movement")
MIN_SAMPLES = 6              # requests heard before he trusts his picture of your usual voice


def _pitch(frame: np.ndarray) -> float | None:
    """Fundamental frequency of one voiced 40 ms frame (autocorrelation), or None."""
    x = frame - frame.mean()
    c = np.correlate(x, x, "full")[len(x) - 1:]
    lo, hi = SR // 400, SR // 70
    if c[0] <= 0 or hi >= len(c):
        return None
    k = lo + int(np.argmax(c[lo:hi]))
    return SR / k if c[k] > 0.45 * c[0] else None


def measure(audio: np.ndarray, text: str) -> dict | None:
    """Speaking rate (words a second), loudness (dB), pitch (semitones) and how much it moved."""
    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    words = len((text or "").split())
    if len(a) < SR // 2 or words < 2:
        return None
    n = len(a) // FRAME
    frames = a[: n * FRAME].reshape(n, FRAME)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    loud = rms > max(0.008, float(np.percentile(rms, 90)) * 0.15)
    if loud.sum() < 5:
        return None
    # speaking time: from the first to the last voiced frame, so pauses inside count but trailing silence doesn't
    idx = np.flatnonzero(loud)
    speaking = (idx[-1] - idx[0] + 1) * FRAME / SR
    pitches = [p for f in frames[loud] if (p := _pitch(f)) is not None]
    if len(pitches) < 4:
        return None
    st = 12 * np.log2(np.asarray(pitches) / 100.0)                      # semitones above 100 Hz
    st = st[np.abs(st - np.median(st)) < 12]                            # drop octave mistakes
    return {
        "rate": words / max(speaking, 0.5),
        "loudness": 20 * math.log10(float(np.percentile(rms[loud], 80)) + 1e-6),
        "pitch": float(np.median(st)),
        "movement": float(np.std(st)),
    }


class ToneReader:
    """Keeps a running picture of your usual voice and says how this request differs from it."""

    def __init__(self, baseline: dict | None = None) -> None:
        self.base = {k: dict(v) for k, v in (baseline or {}).items() if k in FEATURES}
        self.count = int((baseline or {}).get("_count", 0))

    def snapshot(self) -> dict:
        return {**self.base, "_count": self.count}

    def _z(self, key: str, value: float) -> float:
        b = self.base.get(key)
        if not b:
            return 0.0
        return (value - b["mean"]) / max(math.sqrt(b["var"]), {"rate": 0.25, "loudness": 2.0, "pitch": 0.8,
                                                               "movement": 0.4}[key])

    def _learn(self, feats: dict) -> None:
        self.count += 1
        alpha = max(0.08, 1.0 / self.count)          # quick at first, then a slow-moving average
        for k in FEATURES:
            v = feats[k]
            b = self.base.get(k)
            if b is None:
                self.base[k] = {"mean": v, "var": 0.0}
                continue
            d = v - b["mean"]
            b["mean"] += alpha * d
            b["var"] = (1 - alpha) * (b["var"] + alpha * d * d)

    def read(self, audio: np.ndarray, text: str) -> str | None:
        """A few plain words on how this sounded compared with usual, or None if it sounded like you."""
        feats = measure(audio, text)
        if feats is None:
            return None
        known = self.count >= MIN_SAMPLES
        z = {k: self._z(k, feats[k]) for k in FEATURES} if known else {}
        self._learn(feats)
        if not known:
            return None
        fast, slow = z["rate"] > 1.3, z["rate"] < -1.3
        loud, quiet = z["loudness"] > 1.3, z["loudness"] < -1.3
        high = z["pitch"] > 1.3
        lively, flat = z["movement"] > 1.3, z["movement"] < -1.2
        if fast and (loud or high):
            return "hurried and a little tense"
        if loud and high and lively:
            return "animated: excited, or perhaps annoyed"
        if loud and (high or fast):
            return "raised voice, perhaps frustrated"
        if fast:
            return "in a hurry"
        if quiet and (slow or flat):
            return "quiet and slow, perhaps tired"
        if flat and slow:
            return "flat and slow, perhaps tired or fed up"
        if quiet:
            return "speaking softly"
        if lively and not loud:
            return "lively, in good spirits"
        if slow:
            return "unhurried, taking their time"
        return None
