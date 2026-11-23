"""Knowing when you've finished speaking: Smart Turn features and the listening loop around it."""
import asyncio

import numpy as np
import pytest

from jarvis.voice import turn
from jarvis.voice.audio import FRAME, SAMPLE_RATE, record_utterance


def test_features_match_whisper_reference():
    # golden values from transformers.WhisperFeatureExtractor(chunk_length=8) on this exact signal
    n = 16000 * 2
    t = np.arange(n) / 16000
    a = (0.3 * np.sin(2 * np.pi * 220 * t) + 0.1 * np.sin(2 * np.pi * 1250 * t) * np.cos(2 * np.pi * 2 * t)).astype(np.float32)
    f = turn.features(a)[0]
    assert f.shape == (80, 800) and f.dtype == np.float32
    got = [f.mean(), f.std(), f[10, 700], f[40, 650], f[70, 799], f[5, 100]]
    want = [-0.12749, 0.3217, 0.5896, -0.2168, -0.08721, -0.2168]
    assert np.allclose(got, want, atol=2e-4), got


def test_features_keep_the_last_eight_seconds():
    tail = np.sin(np.arange(16000 * 3) / 7).astype(np.float32)
    long = np.concatenate([np.random.default_rng(1).standard_normal(16000 * 9).astype(np.float32), tail])
    assert np.allclose(turn.features(long), turn.features(long[-16000 * 8:]), atol=1e-6)


def test_mel_filters_shape_and_coverage():
    fb = turn.mel_filters()
    assert fb.shape == (201, 80) and (fb >= 0).all()
    assert (fb.sum(axis=0) > 0).all()                      # every mel band hears something


def test_missing_model_means_no_verdict(tmp_path):
    d = turn.TurnDetector(tmp_path)
    assert d.load() is False and d.probability(np.zeros(16000, np.float32)) is None


# ── the listening loop ─────────────────────────────────────────────────
class EnergyVAD:
    def is_speech(self, frame):
        return float(np.sqrt(np.mean((frame.astype(np.float32) / 32768) ** 2))) > 0.02


def frames(pattern):
    """pattern: list of (seconds, loud?) → 80 ms int16 frames."""
    out = []
    for secs, loud in pattern:
        for _ in range(int(round(secs / (FRAME / SAMPLE_RATE)))):
            x = (np.sin(np.arange(FRAME) / 3) * (9000 if loud else 0)).astype(np.int16)
            out.append(x)
    return out


class Turn:
    def __init__(self, answers):
        self.answers, self.calls, self.available = list(answers), [], True

    def probability(self, audio):
        self.calls.append(len(audio) / SAMPLE_RATE)
        return self.answers.pop(0) if self.answers else 0.9


def run(pattern, turn_model=None, **kw):
    async def go():
        q = asyncio.Queue()
        fs = frames(pattern)
        for f in fs:
            q.put_nowait(f)
        audio = await record_utterance(q, EnergyVAD(), start_timeout=5, end_silence=0.8, turn=turn_model, **kw)
        return audio, q.qsize()
    return asyncio.run(go())


def listened(pattern, left):
    """Seconds of audio the recorder consumed before it decided you'd finished."""
    return sum(secs for secs, _ in pattern) - left * FRAME / SAMPLE_RATE


def test_finished_thought_ends_quickly():
    t = Turn([0.93])
    audio, left = run([(1.0, True), (3.0, False)], t)
    assert len(t.calls) == 1
    assert len(audio) / SAMPLE_RATE < 1.0 + 0.6 + 0.4          # stopped ~0.25 s into the pause, not 0.8
    assert left > 0                                             # the rest of the silence wasn't waited out


def test_mid_thought_pause_is_waited_out_and_the_rest_is_kept():
    t = Turn([0.08, 0.95])               # "set a timer for…" (pause) "…ten minutes"
    audio, _ = run([(0.8, True), (1.2, False), (0.8, True), (2.0, False)], t)
    assert len(t.calls) == 2
    assert len(audio) / SAMPLE_RATE > 0.8 + 1.2 + 0.8           # both halves of the sentence are in


def test_mid_thought_gives_up_after_the_patience_window():
    t = Turn([0.1])
    pattern = [(1.0, True), (4.0, False)]
    audio, left = run(pattern, t, turn_patience=1.5)
    assert 1.0 + 1.4 <= listened(pattern, left) <= 1.0 + 2.0 and left > 0
    assert len(audio) / SAMPLE_RATE < 1.0 + 0.6                 # what it hands over stops after your words


def test_an_unsure_verdict_waits_the_normal_pause():
    t = Turn([0.55])                       # neither clearly finished nor clearly mid-thought
    pattern = [(1.0, True), (3.0, False)]
    audio, left = run(pattern, t)
    assert 1.0 + 0.7 <= listened(pattern, left) <= 1.0 + 1.3


def test_without_a_model_the_silence_timeout_applies():
    pattern = [(1.0, True), (3.0, False)]
    audio, left = run(pattern, None)
    assert 1.0 + 0.7 <= listened(pattern, left) <= 1.0 + 1.3


def test_push_to_talk_ignores_the_model():
    async def go():
        q = asyncio.Queue()
        for f in frames([(1.0, True), (0.5, False)]):
            q.put_nowait(f)
        stop = asyncio.Event()
        t = Turn([0.99])
        task = asyncio.create_task(record_utterance(q, EnergyVAD(), end_silence=30, stop_event=stop, turn=t))
        await asyncio.sleep(0.2)
        stop.set()
        audio = await asyncio.wait_for(task, 3)
        return t, audio
    t, audio = asyncio.run(go())
    assert t.calls == [] and audio is not None


def test_real_model_when_available(tmp_path):
    """Runs the real Smart Turn model if it's on this machine (the Mac self-test always does)."""
    import os
    from pathlib import Path

    src = os.environ.get("JARVIS_SMART_TURN_MODEL")
    if not src or not Path(src).exists():
        pytest.skip("set JARVIS_SMART_TURN_MODEL to the .onnx to run this")
    (tmp_path / "turn").mkdir()
    (tmp_path / "turn" / turn.MODEL_FILE).write_bytes(Path(src).read_bytes())
    d = turn.TurnDetector(tmp_path)
    p = d.probability(np.sin(np.arange(16000) / 5).astype(np.float32) * 0.3)
    assert d.available and p is not None and 0.0 <= p <= 1.0


# ── "Sir?" only after a pause, never over your voice ───────────────────
def test_acknowledgement_is_a_prepared_phrase_and_the_echo_is_dropped():
    from types import SimpleNamespace

    from jarvis.core.orchestrator import Jarvis

    played = []

    class Speaker:
        def say_now(self, text):
            played.append(text)
            return True

        async def wait_done(self, timeout=None):
            pass

        def play_tone(self, kind):
            played.append(f"tone:{kind}")

    from jarvis.voice.audio import VAD

    vad = VAD()
    vad._vad = None
    j = SimpleNamespace(cfg=SimpleNamespace(get=lambda k, d=None: "sir"), ACKS=Jarvis.ACKS, speaker=Speaker(), vad=vad)
    j.ack_phrases = lambda: Jarvis.ack_phrases(j)
    j._settle_keep = lambda q, **kw: Jarvis._settle_keep(j, q, **kw)
    j.echo_cancelled = True

    async def go():
        q = asyncio.Queue()
        for _ in range(5):
            q.put_nowait(np.zeros(FRAME, np.int16))      # what the mic heard while he spoke
        for _ in range(4):
            await Jarvis._acknowledge(j, q)
        return q.qsize()

    left = asyncio.run(go())
    assert left == 0 and len(played) == 4 and all(p in Jarvis.ack_phrases(j) for p in played)
    assert all(a != b for a, b in zip(played, played[1:]))
    assert "Sir?" in Jarvis.ack_phrases(j) and "Yes, sir?" in Jarvis.ack_phrases(j)


def test_speaker_plays_prepared_phrases_instantly():
    import time

    from jarvis.voice import tts

    class Backend:
        name, sample_rate = "fake", 24000

        def synth(self, text):
            return np.full(2400, 0.1, dtype=np.float32)

    sp = tts.Speaker(Backend())
    sp._sd = None
    sp.start()
    assert sp.say_now("Sir?") is False                    # not ready yet: caller falls back to a tone
    sp.prepare(["Sir?", "Mm?"])
    for _ in range(50):
        if "Mm?" in sp._cache:
            break
        time.sleep(0.02)
    assert sp.say_now("Sir?") is True
    assert sp._idle.wait(3)
    sp.close()


def test_words_that_cant_end_a_request():
    from jarvis.voice.turn import sounds_unfinished

    for t in ("Remind me to", "Send it to Pepper and", "Set a timer for", "um", "Play something by",
              "Text Happy that I'll be late, because", "Open the,", "Book it for the…"):
        assert sounds_unfinished(t), t
    for t in ("What's the weather like?", "Turn the lights on.", "Yes.", "The first one", "Call me",
              "Remind me to call the bank.", "Log in", "Do that", "", "No, the other one."):
        assert not sounds_unfinished(t), t
