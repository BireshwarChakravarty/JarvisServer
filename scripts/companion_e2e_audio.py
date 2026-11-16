"""A fake microphone recording for the companion end-to-end test: a pause, ~1.5 s of "speech", silence."""
import sys
import wave

import numpy as np

sr = 48000
t = np.arange(int(sr * 1.5)) / sr
tone = 0.35 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
x = np.concatenate([np.zeros(int(sr * 0.8)), tone, np.zeros(int(sr * 12))])
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes((x * 32767).astype("<i2").tobytes())
