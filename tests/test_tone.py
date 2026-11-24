"""Reading your tone of voice, compared with how you usually sound."""
import asyncio

import numpy as np

from jarvis.voice.tone import MIN_SAMPLES, ToneReader, measure

SR = 16000


def voice(seconds=2.0, f0=120.0, wobble=1.5, level=0.1, seed=0):
    """A voiced, speech-like signal: a harmonic tone whose pitch moves by `wobble` semitones, in syllables."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(SR * seconds)) / SR
    pitch = f0 * 2 ** (wobble * np.sin(2 * np.pi * 0.7 * t + rng.uniform(0, 6)) / 12)
    phase = 2 * np.pi * np.cumsum(pitch) / SR
    a = sum(np.sin(k * phase) / k for k in range(1, 6))
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t) ** 2          # syllables
    return (level * a * envelope / 1.6).astype(np.float32)


USUAL = "could you check the weather for tomorrow morning"           # 8 words in about 2 s: 4 a second


def trained():
    r = ToneReader()
    for i in range(MIN_SAMPLES + 2):
        assert r.read(voice(2.0, 120 + i % 3, seed=i), USUAL) is None    # learning your usual voice: says nothing
    return r


def test_it_measures_rate_pitch_and_loudness():
    m = measure(voice(2.0, 120), USUAL)
    assert 3.5 < m["rate"] < 4.6 and 0 < m["pitch"] < 6 and m["movement"] > 0.3
    assert measure(voice(0.3), USUAL) is None and measure(voice(2.0), "hi") is None


def test_your_usual_voice_is_not_remarked_on():
    r = trained()
    assert r.read(voice(2.0, 121, seed=99), USUAL) is None


def test_hurried_and_tense():
    r = trained()
    fast = "could you just check the weather for tomorrow morning and my calendar and the traffic now please"
    assert r.read(voice(2.0, 150, level=0.2, seed=7), fast) == "hurried and a little tense"


def test_quiet_and_tired():
    r = trained()
    assert r.read(voice(3.2, 108, wobble=0.4, level=0.03, seed=8), USUAL) == "quiet and slow, perhaps tired"


def test_the_brain_is_told_how_you_sound(cfg):
    from jarvis.core.orchestrator import Jarvis

    app = Jarvis(cfg, headless=True)
    app._tone = trained()
    asyncio.run(app._read_tone(voice(3.2, 108, wobble=0.4, level=0.03, seed=8), USUAL))
    ctx = asyncio.run(app._live_context())
    assert ctx["how they sound"].startswith("quiet and slow")
