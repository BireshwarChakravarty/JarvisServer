"""His own voice in the mic: removed when it clearly is his, never when it's you answering him."""
import asyncio

import numpy as np

from jarvis.voice.audio import FRAME, VAD, settle
from jarvis.voice.echo import strip_echo

SAID = ["Ten minutes, on the clock.", "Shall I send it to Pepper?"]


def test_his_words_alone_are_dropped():
    assert strip_echo("send it to Pepper", SAID) == ""
    assert strip_echo("on the clock", SAID) == ""


def test_his_tail_then_yours_keeps_yours():
    assert strip_echo("to Pepper? Yes, and copy Happy", SAID) == "Yes, and copy Happy"
    assert strip_echo("the clock. What's the weather?", SAID) == "What's the weather?"


def test_short_answers_to_him_are_kept():
    assert strip_echo("send it", SAID) == "send it"              # you, answering "Shall I send it?"
    assert strip_echo("yes", SAID) == "yes"
    assert strip_echo("Pepper", SAID) == "Pepper"


def test_unrelated_speech_is_untouched():
    assert strip_echo("what's on my calendar tomorrow", SAID) == "what's on my calendar tomorrow"
    assert strip_echo("anything", []) == "anything"
    assert strip_echo("", SAID) == ""


def quiet_vad():
    v = VAD()
    v._vad = None
    return v


def frames(kind, n):
    if kind == "silence":
        return [np.zeros(FRAME, np.int16) for _ in range(n)]
    t = np.arange(FRAME) / 16000
    return [(8000 * np.sin(2 * np.pi * 200 * t)).astype(np.int16) for _ in range(n)]


def test_settle_eats_the_echo_tail_but_stops_at_quiet():
    async def go():
        q = asyncio.Queue()
        for f in frames("speech", 4) + frames("silence", 3) + frames("speech", 10):
            q.put_nowait(f)
        await settle(q, quiet_vad(), quiet=0.24, max_wait=2)
        return q.qsize()
    assert asyncio.run(go()) == 10                               # the tail went; what came after is kept


def test_settle_gives_up_after_max_wait():
    async def go():
        q = asyncio.Queue()
        for f in frames("speech", 30):
            q.put_nowait(f)
        t = asyncio.get_running_loop().time()
        await settle(q, quiet_vad(), quiet=0.24, max_wait=0.3)
        return asyncio.get_running_loop().time() - t
    assert asyncio.run(go()) < 0.5
