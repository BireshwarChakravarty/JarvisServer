"""A streaming natural voice: whole passages, played while they're generated, stoppable at once."""
import threading
import time

import numpy as np

from jarvis.voice.tts import Speaker


class StreamingVoice:
    """Generates 0.1 s chunks at `rtf` x real time and records what it was asked to say."""
    name = "qwen3"
    sample_rate = 24000
    streams = True

    def __init__(self, rtf=0.3):
        self.rtf, self.passages, self.chunks_made = rtf, [], 0
        self.gate = threading.Event()
        self.gate.set()

    def synth(self, text):
        return np.zeros(2400, np.float32)

    def synth_stream(self, text):
        self.passages.append(text)
        for _ in range(max(1, len(text) // 8)):          # ~0.1 s of speech per 8 characters
            self.gate.wait(5)
            time.sleep(0.1 * self.rtf)
            self.chunks_made += 1
            yield np.full(2400, 0.1, np.float32)


def speaker(voice):
    sp = Speaker(voice)
    sp._sd = None              # headless: playback keeps time without a device
    sp.start()
    return sp


def test_sentences_waiting_together_are_spoken_as_one_passage():
    v = StreamingVoice()
    v.gate.clear()                                       # hold the first passage while the rest queues
    sp = speaker(v)
    sp.enqueue("Right.")
    time.sleep(0.1)
    for s in ("Two meetings this afternoon, the first at half past two.", "And it's drizzling.",
              "I'd take the umbrella."):
        sp.enqueue(s)
    v.gate.set()
    sp._idle.wait(10)
    assert v.passages[0] == "Right."
    assert v.passages[1] == ("Two meetings this afternoon, the first at half past two. And it's drizzling. "
                             "I'd take the umbrella.")
    assert not sp.speaking


def test_it_plays_before_the_passage_is_finished():
    v = StreamingVoice(rtf=0.5)
    sp = speaker(v)
    sp.turn_mark = time.monotonic()
    sp.enqueue("This is a fairly long reply that takes a while to generate in full, sir, so it streams.")
    t0 = time.monotonic()
    while not getattr(sp, "latencies", None) and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    first = time.monotonic() - t0
    assert sp.latencies and first < 0.6, first           # first sound long before the ~5 s passage is done
    assert v.chunks_made < 11 * 0.5                      # most of it still to be generated at that point
    sp._idle.wait(15)


def test_a_stop_ends_generation_too():
    v = StreamingVoice(rtf=1.0)
    sp = speaker(v)
    sp.enqueue("A very long answer that keeps going and going for quite a while before it ends, sir, really.")
    time.sleep(0.4)
    sp.stop()
    made = v.chunks_made
    time.sleep(0.5)
    assert v.chunks_made <= made + 1                      # no more generation after the stop
    assert not sp.speaking


def test_the_head_start_fits_the_voices_speed():
    v = StreamingVoice()
    sp = Speaker(v)
    passage = "x" * 150                                   # about ten seconds of speech
    v.rtf = 0.4
    assert sp._head_start(passage) == 0.35                # comfortably faster than speech: start at once
    v.rtf = 0.95                                          # just under real time (the M6 measured 0.94)
    assert 0.35 < sp._head_start(passage) < 2.5           # a moment in hand, then it never runs dry
    v.rtf = None
    assert sp._head_start(passage) == 0.35
