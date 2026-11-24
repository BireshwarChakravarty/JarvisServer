"""Voice lab (runs on the Mac build machine, not in the app).

Designs candidate JARVIS voices with Qwen3-TTS VoiceDesign from written descriptions (original voices,
not anyone's real voice), then times the small Qwen3-TTS Base model speaking in each of them on Apple
Silicon: how soon the first sound comes and how fast it runs against real time. Writes WAV samples and
results.json to the output folder.

    python scripts/voice_lab.py out/
"""
from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "voice-lab")
OUT.mkdir(parents=True, exist_ok=True)

DESIGNER = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"
# The app's natural voice models (the larger first): samples are rendered with the one he'll use.
SPEAKERS = ["mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit", "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit"]

# What the reference recording says (the cloning model is given this transcript with it).
REF_TEXT = ("Good evening, sir. I've taken the liberty of moving your three o'clock to tomorrow morning. "
            "The weather, I'm pleased to say, looks rather promising for the weekend.")

# Round 2: a FRIDAY-like voice. Original voices designed from written descriptions only: no recording of
# any real person is used. (Round 1 designed Butler and Gentleman, which the app ships; see git history.)
CANDIDATES = {
    "friday": "A woman in her early thirties with a soft, natural Irish accent. Warm, quick and quietly "
              "confident: an efficient, friendly colleague with a light, playful edge. Clear mid-range voice, "
              "relaxed conversational pace, never like a narrator.",
    "friday_bright": "A bright, energetic young Irish woman with a lilting Dublin accent. Crisp and upbeat, "
                     "quick-witted and a little cheeky, speaking briskly but clearly, like someone on top of "
                     "everything and enjoying it.",
    "friday_calm": "A calm, composed Irish woman in her thirties with a gentle, low-key accent. Smooth, warm and "
                   "dry-humoured; unflappable and reassuring, speaking at an easy pace with natural pauses.",
}

LINES = [
    "Ah, there you are. Two meetings this afternoon, the first at half past two, and it's drizzling, so I'd take "
    "the umbrella.",
    "Done. The lights are off and the door's locked.",
    "Hm, the kitchen lights or all of them?",
    "Right. Pepper's message says the board call moved to Thursday, and she'd like your notes before then. "
    "Shall I draft a reply?",
]


def save(path: Path, audio, sr: int) -> float:
    import soundfile as sf

    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    sf.write(str(path), a, sr)
    return len(a) / sr


def main() -> None:
    import mlx.core as mx
    from mlx_audio.tts.utils import load_model

    info = {"python": sys.version.split()[0], "machine": platform.machine(), "mac": platform.mac_ver()[0],
            "device": str(mx.default_device()), "metal": bool(getattr(mx.metal, "is_available", lambda: False)())}
    try:
        import mlx_audio

        info["mlx_audio"] = getattr(mlx_audio, "__version__", "?")
    except Exception:
        pass
    print(json.dumps(info), flush=True)
    results: dict = {"info": info, "design": {}, "speak": {}}

    t = time.perf_counter()
    designer = load_model(DESIGNER)
    results["design_load_s"] = round(time.perf_counter() - t, 1)
    for name, description in CANDIDATES.items():
        t = time.perf_counter()
        chunks = [r.audio for r in designer.generate(text=REF_TEXT, instruct=description, lang_code="english")]
        took = time.perf_counter() - t
        audio = np.concatenate([np.asarray(c, dtype=np.float32).reshape(-1) for c in chunks])
        secs = save(OUT / f"ref_{name}.wav", audio, designer.sample_rate)
        results["design"][name] = {"seconds": round(secs, 2), "took_s": round(took, 2), "rtf": round(took / secs, 2)}
        print("design", name, results["design"][name], flush=True)
    del designer
    mx.clear_cache() if hasattr(mx, "clear_cache") else None

    for repo in SPEAKERS:
        short = repo.split("/")[-1]
        t = time.perf_counter()
        model = load_model(repo)
        load_s = time.perf_counter() - t
        runs = []
        for name in CANDIDATES:
            if short != SPEAKERS[0].split("/")[-1] and name != "friday":
                continue                      # timing only for the lighter model: one voice is enough
            for i, line in enumerate(LINES):
                t = time.perf_counter()
                first = None
                chunks = []
                for r in model.generate(text=line, ref_audio=str(OUT / f"ref_{name}.wav"), ref_text=REF_TEXT,
                                        lang_code="english", stream=True, streaming_interval=0.32):
                    if first is None:
                        first = time.perf_counter() - t
                    chunks.append(np.asarray(r.audio, dtype=np.float32).reshape(-1))
                took = time.perf_counter() - t
                secs = save(OUT / f"{short}_{name}_{i + 1}.wav", np.concatenate(chunks), model.sample_rate)
                run = {"voice": name, "line": i + 1, "seconds": round(secs, 2), "took_s": round(took, 2),
                       "first_audio_s": round(first or took, 2), "rtf": round(took / max(secs, 0.01), 2)}
                runs.append(run)
                print(short, run, flush=True)
        results["speak"][short] = {"load_s": round(load_s, 1), "runs": runs}
        del model
        mx.clear_cache() if hasattr(mx, "clear_cache") else None

    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    print("done", flush=True)


if __name__ == "__main__":
    main()
