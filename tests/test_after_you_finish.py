"""When you've finished, he stops listening: the room (a TV, people next door, a fan) doesn't keep the
recording going, isn't transcribed with your words, and doesn't count as you in a follow-up."""
import asyncio

import numpy as np

from jarvis.voice.audio import FRAME, SAMPLE_RATE, VAD, level, record_utterance
from jarvis.voice.stt import _real


def tone(amp, n):
    t = np.arange(FRAME) / SAMPLE_RATE
    return [(amp * np.sin(2 * np.pi * 190 * t)).astype(np.int16) for _ in range(n)]


def silence(n):
    return [np.zeros(FRAME, np.int16) for _ in range(n)]


def energy_vad():
    v = VAD()
    v._vad = None
    return v


def record(frames, **kw):
    async def go():
        q = asyncio.Queue()
        for f in frames:
            q.put_nowait(f)
        audio = await record_utterance(q, energy_vad(), start_timeout=2, end_silence=1.0, **kw)
        return audio, q.qsize()
    return asyncio.run(go())


def test_a_tv_after_you_doesnt_keep_the_recording_going():
    frames = tone(6000, 12) + tone(900, 40) + silence(10)          # you, then the TV for 3.2 s
    audio, left = record(frames)
    consumed = len(frames) - left
    assert consumed <= 12 + 14, consumed                             # ended about 1 s after your last word
    tail = audio[12 * FRAME:]
    assert len(tail) and np.max(np.abs(tail)) == 0                    # and the TV isn't in what's transcribed


def test_your_own_quieter_words_still_count():
    audio, _ = record(tone(6000, 10) + tone(2500, 8) + silence(16))  # trailing off, -8 dB: still you
    assert np.max(np.abs(audio[10 * FRAME:18 * FRAME])) > 0.05


def test_a_follow_up_floor_ignores_the_room_entirely():
    audio, _ = record(tone(900, 20) + silence(30), min_level=0.04)  # only the TV: nothing starts
    assert audio is None
    audio, _ = record(tone(6000, 10) + silence(16), min_level=0.04)
    assert audio is not None


def test_the_room_in_audio_heard_before_a_follow_up_isnt_you():
    """His voice fading, then the TV, captured before the follow-up began (a slow Mac): only the loud
    part is kept, and the TV never reaches speech recognition."""
    pre = tone(2203, 4) + tone(900, 8)
    audio, _ = record(silence(16), preroll=pre, in_speech=True, min_level=0.04)
    assert audio is not None and np.max(np.abs(audio[4 * FRAME:])) == 0
    audio, _ = record(silence(30), preroll=tone(900, 8), in_speech=True, min_level=0.04)
    assert audio is None                                              # nothing loud enough: nobody spoke


def test_level_is_how_loud_the_voice_was():
    a = np.concatenate(tone(6000, 10) + silence(3)).astype(np.float32) / 32768
    assert 0.12 < level(a) < 0.14 and level(None) == 0.0


def test_whispers_invented_words_are_dropped():
    assert _real(0.1, -0.3)                    # speech
    assert not _real(0.9, -1.4)                # 'no speech here' and low confidence: invented
    assert _real(0.9, -0.2)                    # confident words in a quiet clip: keep
    assert _real(None, None)                   # a backend that doesn't say: keep
