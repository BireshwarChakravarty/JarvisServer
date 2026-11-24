"""Mood lab (runs on the Mac build machine, not in the app): expression in JARVIS's voices.

Qwen3-TTS follows style instructions only when it designs a voice, not when it speaks in a cloned one;
there, the delivery follows the reference recording. So each voice gets a small bank of reference
recordings, one per mood, and a reply is spoken from the one that fits. Two ways to make them:

  designed  VoiceDesign, from the voice's original written description plus how to deliver the line
            ("warmly, with a smile in the voice"): most expressive, may drift from the voice
  spoken    the voice itself (Base model, cloning its neutral recording) saying a script written in that
            mood: keeps the voice, the mood comes from the words

Then each voice speaks a reply suited to each mood from each mood recording, streamed as the app will.

    python scripts/mood_lab.py out/ jarvis/voice/voices
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "mood-lab")
VOICES_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else "jarvis/voice/voices")
OUT.mkdir(parents=True, exist_ok=True)

DESIGNER = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"
SPEAKER = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit"
REF_TEXT = ("Good evening, sir. I've taken the liberty of moving your three o'clock to tomorrow morning. "
            "The weather, I'm pleased to say, looks rather promising for the weekend.")

# The written descriptions the voices were designed from (scripts/voice_lab.py, rounds 1 and 2).
VOICES = {
    "butler": "A refined middle-aged English butler with a Received Pronunciation accent. Calm, dry and quietly "
              "witty; warm but restrained. Unhurried, precise diction, a smooth baritone. A trusted gentleman's "
              "valet speaking softly to his employer.",
    "gentleman": "A distinguished English gentleman with a deep, warm voice and a London RP accent. Unflappable "
                 "and kind, speaking at an easy pace like a person in conversation, not a narrator.",
    "friday": "A woman in her early thirties with a soft, natural Irish accent. Warm, quick and quietly "
              "confident: an efficient, friendly colleague with a light, playful edge. Clear mid-range voice, "
              "relaxed conversational pace, never like a narrator.",
    "friday_calm": "A calm, composed Irish woman in her thirties with a gentle, low-key accent. Smooth, warm and "
                   "dry-humoured; unflappable and reassuring, speaking at an easy pace with natural pauses.",
}

# Each mood: how to deliver it (for designing), the script its reference recording says, and a reply
# to try it on.
MOODS = {
    "warm": {
        "how": "Speaking warmly and softly, with a smile in the voice, glad to see them.",
        "script": "Ah, there you are, sir. It's good to have you home. I've kept the evening clear, and the "
                  "kettle's on, if you'd like a cup.",
        "reply": "Welcome back. I've dimmed the lights and put on something quiet for you.",
    },
    "light": {
        "how": "Speaking brightly and playfully, lightly amused, with a little lift in the voice.",
        "script": "Well, that went rather splendidly, sir. Three in a row, no less. I'd say that calls for a "
                  "small celebration, wouldn't you?",
        "reply": "Done. And for the record, that's the fastest you've ever cleared your inbox.",
    },
    "serious": {
        "how": "Speaking seriously and firmly, focused and calm, with quiet urgency.",
        "script": "Sir, I need your attention for a moment. The server has been down for twenty minutes, and two "
                  "clients are waiting. I'd suggest we deal with it now.",
        "reply": "The backup failed last night. I'd fix it before the board call.",
    },
    "sympathetic": {
        "how": "Speaking gently and softly, slowly, with real sympathy and care.",
        "script": "I'm so sorry, sir. That's a rotten piece of news. Take whatever time you need. I'll hold "
                  "everything else until you're ready.",
        "reply": "I'm sorry. That's hard news. I'll keep things quiet for the rest of the evening.",
    },
}
NEUTRAL_REPLY = "Your three o'clock moved to tomorrow, and the weather looks fine for the weekend."


def save(path: Path, audio, sr: int) -> float:
    import soundfile as sf

    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    sf.write(str(path), a, sr)
    return len(a) / sr


def pitch_stats(a: np.ndarray, sr: int) -> dict:
    fr, f0 = int(0.04 * sr), []
    for i in range(0, len(a) - fr, fr // 2):
        x = a[i:i + fr]
        if np.sqrt(np.mean(x * x)) < 0.03:
            continue
        x = x - x.mean()
        c = np.correlate(x, x, "full")[fr - 1:]
        lo, hi = int(sr / 400), int(sr / 70)
        k = lo + int(np.argmax(c[lo:hi]))
        if c[k] > 0.5 * c[0]:
            f0.append(sr / k)
    if not f0:
        return {}
    f0 = np.array(f0)
    st = 12 * np.log2(f0 / np.median(f0))
    st = st[np.abs(st) < 12]
    return {"median_hz": round(float(np.median(f0))), "movement_st": round(float(np.std(st)), 2)}


def joined(results) -> np.ndarray:
    return np.concatenate([np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in results])


def main() -> None:
    import mlx.core as mx
    from mlx_audio.tts.utils import load_model

    report: dict = {"refs": {}, "replies": {}}

    # 1 · designed mood references
    t = time.perf_counter()
    designer = load_model(DESIGNER)
    print("designer loaded", round(time.perf_counter() - t, 1), flush=True)
    for voice, desc in VOICES.items():
        for mood, m in MOODS.items():
            audio = joined(designer.generate(text=m["script"], instruct=f"{desc} {m['how']}", lang_code="english"))
            secs = save(OUT / f"ref_{voice}_{mood}_designed.wav", audio, designer.sample_rate)
            report["refs"][f"{voice}/{mood}/designed"] = {"seconds": round(secs, 1),
                                                          **pitch_stats(audio, designer.sample_rate)}
            print("designed", voice, mood, report["refs"][f"{voice}/{mood}/designed"], flush=True)
    del designer
    mx.clear_cache()

    # 2 · spoken mood references, then replies from every reference
    speaker = load_model(SPEAKER)
    sr = speaker.sample_rate
    for voice in VOICES:
        neutral = str(VOICES_DIR / f"{voice}.wav")
        report["refs"][f"{voice}/neutral"] = pitch_stats(
            np.asarray(__import__("soundfile").read(neutral)[0], dtype=np.float32), sr)
        for mood, m in MOODS.items():
            audio = joined(speaker.generate(text=m["script"], ref_audio=neutral, ref_text=REF_TEXT,
                                            lang_code="english"))
            secs = save(OUT / f"ref_{voice}_{mood}_spoken.wav", audio, sr)
            report["refs"][f"{voice}/{mood}/spoken"] = {"seconds": round(secs, 1), **pitch_stats(audio, sr)}
            print("spoken", voice, mood, report["refs"][f"{voice}/{mood}/spoken"], flush=True)

        def speak(text: str, ref: str, ref_text: str) -> np.ndarray:
            return joined(speaker.generate(text=text, ref_audio=ref, ref_text=ref_text, lang_code="english",
                                           stream=True, streaming_interval=0.4))

        audio = speak(NEUTRAL_REPLY, neutral, REF_TEXT)
        save(OUT / f"reply_{voice}_neutral.wav", audio, sr)
        report["replies"][f"{voice}/neutral"] = pitch_stats(audio, sr)
        for mood, m in MOODS.items():
            for how in ("designed", "spoken"):
                audio = speak(m["reply"], str(OUT / f"ref_{voice}_{mood}_{how}.wav"), m["script"])
                save(OUT / f"reply_{voice}_{mood}_{how}.wav", audio, sr)
                report["replies"][f"{voice}/{mood}/{how}"] = pitch_stats(audio, sr)
                print("reply", voice, mood, how, report["replies"][f"{voice}/{mood}/{how}"], flush=True)
        (OUT / "results.json").write_text(json.dumps(report, indent=2))
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
    print("done", flush=True)


if __name__ == "__main__":
    main()
