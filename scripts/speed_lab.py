"""Speed lab (runs on the Mac build machine, not in the app): how fast can the natural voice be?

Times every way of speaking a reply in JARVIS's designed voices on Apple Silicon, and saves what each
one sounds like so the choice can be made by ear as well as by the clock:

  qwen-sentence   Qwen3-TTS 0.6B 4-bit, one call per sentence, not streamed (what 2.0.23 does)
  qwen-stream     the same model, the whole reply in one streamed call (no re-decoding of the
                  reference recording for every sentence)
  qwen8-stream    the same, 8-bit weights
  pocket          Kyutai Pocket TTS (100M, MLX), cloning the designed voices from their recordings,
                  streamed, with each voice's conditioning prepared once and reused
  pocket8         the same, 8-bit weights

For each: load time, time to the first sound, and the real-time factor (time taken / audio length,
lower is faster; below 1.0 keeps up with speech) on a short, a medium and a long reply.

    python scripts/speed_lab.py out/ jarvis/voice/voices
"""
from __future__ import annotations

import json
import platform
import re
import sys
import time
from pathlib import Path

import numpy as np

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "speed-lab")
VOICES_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else "jarvis/voice/voices")
OUT.mkdir(parents=True, exist_ok=True)

REF_TEXT = ("Good evening, sir. I've taken the liberty of moving your three o'clock to tomorrow morning. "
            "The weather, I'm pleased to say, looks rather promising for the weekend.")
VOICES = ["butler", "gentleman", "friday", "friday_calm"]
REPLIES = {
    "short": "Done. The lights are off.",
    "medium": "Right. Two meetings this afternoon, the first at half past two, and it's drizzling, so I'd take "
              "the umbrella.",
    "long": "Morning. You've three things today: the board call at ten, which Pepper moved from Thursday, lunch "
            "with Rhodey at one at the usual place, and the dentist at four thirty. It's twelve degrees and "
            "clearing by noon. Two emails need you: the contract from legal wants a signature by Friday, and "
            "the bank has a question about last month's transfer. Shall I draft replies to both?",
}
QWEN4 = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit"
QWEN8 = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
POCKET = "mlx-community/pocket-tts"
POCKET8 = "mlx-community/pocket-tts-8bit"


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def save(path: Path, audio, sr: int) -> None:
    import soundfile as sf

    sf.write(str(path), np.asarray(audio, dtype=np.float32).reshape(-1), sr)


def run(fn) -> dict:
    """fn() yields audio chunks; returns timing and the joined audio."""
    t0 = time.perf_counter()
    first, chunks = None, []
    for a in fn():
        a = np.asarray(a, dtype=np.float32).reshape(-1)
        if first is None and len(a):
            first = time.perf_counter() - t0
        chunks.append(a)
    took = time.perf_counter() - t0
    audio = np.concatenate(chunks) if chunks else np.zeros(1, np.float32)
    return {"took": took, "first": first or took, "audio": audio}


def main() -> None:
    import mlx.core as mx
    from mlx_audio.tts.utils import load_model

    info = {"python": sys.version.split()[0], "machine": platform.machine(), "mac": platform.mac_ver()[0],
            "device": str(mx.default_device())}
    print(json.dumps(info), flush=True)
    results: dict = {"info": info, "engines": {}}

    def bench(label: str, repo: str, make_speaker) -> None:
        mx.clear_cache()
        t = time.perf_counter()
        try:
            model = load_model(repo)
        except Exception as exc:
            results["engines"][label] = {"error": f"load: {type(exc).__name__}: {exc}"[:400]}
            print(label, "LOAD ERROR", exc, flush=True)
            return
        load_s = time.perf_counter() - t
        sr = int(getattr(model, "sample_rate", 24000) or 24000)
        res = {"repo": repo, "load_s": round(load_s, 1), "runs": []}
        try:
            for voice in VOICES:
                speak, prep_s = make_speaker(model, voice)
                list(speak("Good evening."))                    # warm-up: first run compiles kernels
                for kind, text in REPLIES.items():
                    r = run(lambda speak=speak, text=text: speak(text))
                    secs = len(r["audio"]) / sr
                    row = {"voice": voice, "reply": kind, "audio_s": round(secs, 2), "took_s": round(r["took"], 2),
                           "first_s": round(r["first"], 2), "rtf": round(r["took"] / max(secs, 0.1), 3),
                           "voice_prep_s": round(prep_s, 2)}
                    res["runs"].append(row)
                    print(label, json.dumps(row), flush=True)
                    if kind != "short":
                        save(OUT / f"{label}_{voice}_{kind}.wav", r["audio"], sr)
                if label.startswith(("qwen-sentence", "qwen8")) and voice == "butler":
                    break                                       # timing only: one voice is enough here
        except Exception as exc:
            import traceback

            res["error"] = f"{type(exc).__name__}: {exc}"[:400]
            traceback.print_exc()
        rtfs = [r["rtf"] for r in res["runs"] if r["reply"] != "short"]
        res["median_rtf"] = round(float(np.median(rtfs)), 3) if rtfs else None
        res["median_first_s"] = round(float(np.median([r["first_s"] for r in res["runs"]])), 2) if res["runs"] else None
        results["engines"][label] = res
        (OUT / "results.json").write_text(json.dumps(results, indent=2))
        del model

    # ── Qwen3-TTS ────────────────────────────────────────────────────────────────────────────────────
    def qwen_sentence(model, voice):
        ref = str(VOICES_DIR / f"{voice}.wav")

        def speak(text):
            for s in sentences(text):
                for r in model.generate(text=s, ref_audio=ref, ref_text=REF_TEXT, lang_code="english"):
                    yield r.audio
        return speak, 0.0

    def qwen_stream(model, voice):
        ref = str(VOICES_DIR / f"{voice}.wav")

        def speak(text):
            for r in model.generate(text=text, ref_audio=ref, ref_text=REF_TEXT, lang_code="english",
                                    stream=True, streaming_interval=0.4):
                yield r.audio
        return speak, 0.0

    # ── Pocket TTS: the voice's conditioning is computed once and reused ─────────────────────────────
    def pocket(model, voice):
        t = time.perf_counter()
        emb_path = OUT / f"pocket_{voice}.safetensors"
        if not emb_path.exists():
            prompt = model._encode_audio(model._load_audio(str(VOICES_DIR / f"{voice}.wav")))
            mx.eval(prompt)
            mx.save_safetensors(str(emb_path), {"audio_prompt": prompt})
        prep = time.perf_counter() - t

        def speak(text):
            for r in model.generate(text=text, voice=str(emb_path), stream=True, streaming_interval=0.4):
                yield r.audio
        return speak, prep

    bench("pocket", POCKET, pocket)
    bench("pocket8", POCKET8, pocket)
    bench("qwen-stream", QWEN4, qwen_stream)
    bench("qwen8-stream", QWEN8, qwen_stream)
    bench("qwen-sentence", QWEN4, qwen_sentence)

    summary = {k: {"median_rtf": v.get("median_rtf"), "median_first_s": v.get("median_first_s"),
                   "load_s": v.get("load_s"), "error": v.get("error")} for k, v in results["engines"].items()}
    results["summary"] = summary
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    print("SUMMARY", json.dumps(summary, indent=1), flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
