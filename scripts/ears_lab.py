"""Ears lab (runs on the Mac build machine, not in the app): which speech recogniser to use.

Speaks a set of everyday requests with several voices (Kokoro, British and American, male and female),
then transcribes them with each candidate (faster-whisper models on the CPU and on Apple's GPU through
CTranslate2's MPS backend) and reports word error rate and time per request.

    python scripts/ears_lab.py out/
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import numpy as np

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ears-lab")
OUT.mkdir(parents=True, exist_ok=True)

LINES = [
    "Hey Jarvis, what's on my plate today?",
    "Just clear the inbox, then remind me to call the bank at five.",
    "Give me an idea of what my mails and WhatsApp say.",
    "Send a message to Pepper saying I'll be ten minutes late.",
    "What's the weather like in Kolkata tomorrow afternoon?",
    "Play something calm in the living room.",
    "Turn on dark mode and open the second email.",
    "Book a table for four at the Italian place near the office around eight.",
]
VOICES = ["bm_george", "bf_emma", "am_adam", "af_heart", "bm_lewis"]
CANDIDATES = [  # (label, model, device, compute)
    ("small.en cpu", "small.en", "cpu", "int8"),
    ("small.en mps", "small.en", "mps", "float16"),
    ("distil-large-v3 cpu", "distil-large-v3", "cpu", "int8"),
    ("distil-large-v3 mps", "distil-large-v3", "mps", "float16"),
    ("large-v3-turbo cpu", "large-v3-turbo", "cpu", "int8"),
    ("large-v3-turbo mps", "large-v3-turbo", "mps", "float16"),
]


def words(s: str) -> list[str]:
    s = s.lower().replace("whatsapp", "whatsapp").replace("what's", "what is").replace("i'll", "i will")
    return re.findall(r"[a-z0-9]+", s)


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), int)
    d[:, 0] = range(len(r) + 1)
    d[0, :] = range(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r), len(h)] / max(1, len(r))


def main() -> None:
    from kokoro_onnx import Kokoro

    models = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT
    k = Kokoro(str(models / "kokoro-v1.0.onnx"), str(models / "voices-v1.0.bin"))
    clips = []
    for v in VOICES:
        for line in LINES:
            a, sr = k.create(line, voice=v, speed=1.0, lang="en-gb" if v.startswith("b") else "en-us")
            idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr)).astype(int)
            audio = np.asarray(a, np.float32)[idx]
            rng = np.random.default_rng(len(clips))
            audio = audio + rng.normal(0, 0.004, len(audio)).astype(np.float32)      # a quiet room
            clips.append((v, line, audio))
    from faster_whisper import WhisperModel

    results = {}
    for label, model, device, compute in CANDIDATES:
        try:
            t0 = time.perf_counter()
            m = WhisperModel(model, device=device, compute_type=compute, cpu_threads=3)
            load = time.perf_counter() - t0
            m.transcribe(np.zeros(16000, np.float32), language="en")
        except Exception as exc:
            results[label] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            print(label, "ERROR", exc, flush=True)
            continue
        errs, secs, bad = [], [], []
        for v, line, audio in clips:
            t0 = time.perf_counter()
            segs, _ = m.transcribe(audio, language="en", beam_size=1, condition_on_previous_text=False,
                                   without_timestamps=True)
            text = " ".join(s.text.strip() for s in segs)
            secs.append(time.perf_counter() - t0)
            e = wer(line, text)
            errs.append(e)
            if e > 0.15:
                bad.append({"voice": v, "said": line, "heard": text})
        results[label] = {"wer": round(float(np.mean(errs)), 4), "sec_per_request": round(float(np.mean(secs)), 3),
                          "load_s": round(load, 1), "worst": bad[:6]}
        print(label, json.dumps(results[label])[:300], flush=True)
        del m
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    print("done", flush=True)


if __name__ == "__main__":
    main()
