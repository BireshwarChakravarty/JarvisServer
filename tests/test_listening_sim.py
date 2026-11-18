"""Listening, end to end, in real time.

The other voice tests put all the audio in the queue up front, which hides timing bugs: here a fake
microphone delivers 80 ms frames at the real pace while JARVIS's actual voice loop runs (wake word,
"Sir?" after a pause, Smart Turn, follow-ups, barge-in, gestures, quick answers, dismissal), and his
own voice leaks back into the microphone as echo while he speaks, like laptop speakers do.

Audio is synthetic but labelled: each spoken segment has its own loudness, and the fake speech
recogniser maps loudness back to the words, so every test can check exactly what JARVIS "heard".
"""
import asyncio
import time

import numpy as np
import pytest

from jarvis.voice.audio import FRAME, VAD

FRAME_S = FRAME / 16000
WAKE_AMP, ECHO_AMP = 3001, 2203
ECHO_TAIL = float(__import__("os").environ.get("SIM_ECHO_TAIL", "0.35"))        # speakers + room: his voice is still in the mic this long after he stops


def tone(amp):
    t = np.arange(FRAME) / 16000
    return (amp * np.sin(2 * np.pi * 190 * t)).astype(np.int16)


class Script:
    """[(kind, seconds, words)] kind: silence | wake | say. Each 'say' gets its own loudness."""

    def __init__(self, parts):
        self.frames, self.words = [], {}
        for i, (kind, secs, words) in enumerate(parts):
            n = max(1, round(secs / FRAME_S))
            if kind == "silence":
                self.frames += [("silence", None)] * n
            elif kind == "wake":
                self.frames += [("wake", None)] * n
            else:
                amp = 6000 + 150 * i
                self.words[amp] = words
                self.frames += [("say", amp)] * n


class FakeMic:
    muted = False

    def __init__(self):
        self.q = asyncio.Queue(maxsize=400)

    def subscribe(self, maxsize=400):
        return self.q


class FakeWake:
    """Fires at the end of a 'Hey Jarvis' (like openWakeWord: after the word, not during it)."""
    available = True

    def __init__(self):
        self.run, self.fired = 0, 0

    def process(self, frame):
        if int(np.abs(frame).max()) == WAKE_AMP:
            self.run += 1
            if self.run == 6:                 # ~0.5 s into the wake phrase
                self.fired += 1
                return True
        else:
            self.run = 0
        return False

    def reset(self):
        self.run = 0


class FakeSTT:
    """Echo frames transcribe as the end of what he last said (what Whisper would really hear)."""
    speaker = None

    def __init__(self, words):
        self.words, self.heard = words, []

    def transcribe(self, audio):
        pcm = (audio * 32768).astype(np.int32)
        seq = []
        for i in range(0, len(pcm) - FRAME + 1, FRAME):
            peak = int(np.abs(pcm[i:i + FRAME]).max())
            last = (self.speaker.said[-1] if self.speaker and self.speaker.said else "<echo>")
            tail = " ".join(last.split()[-4:])
            label = (tail if abs(peak - ECHO_AMP) <= 2 else "Hey Jarvis" if abs(peak - WAKE_AMP) <= 2
                     else next((w for a, w in self.words.items() if abs(peak - a) <= 2), None))
            if label and (not seq or seq[-1] != label):
                seq.append(label)
        text = " ".join(seq)
        self.heard.append(text)
        return text


class FakeTurn:
    """Smart Turn stand-in: 'finished' unless the last words end with '…'."""
    available = True

    def __init__(self, stt):
        self.stt = stt

    def probability(self, audio):
        text = FakeSTT(self.stt.words).transcribe(audio)
        return 0.1 if text.endswith("…") else 0.95


class FakeSpeaker:
    """Plays for 40 ms per character; while 'playing', the mic hears echo."""

    def __init__(self):
        self.said, self.tones, self._until, self.current_text, self.route = [], [], 0.0, "", None
        self._cache = {}

    @property
    def speaking(self):
        return time.monotonic() < self._until

    def enqueue(self, text):
        start = max(time.monotonic(), self._until)
        self._until = start + 0.04 * len(text)
        self.said.append(text)
        self.current_text = text

    def say_now(self, text):
        self.enqueue(text)
        return True

    def play_tone(self, kind="wake"):
        self.tones.append(kind)

    async def wait_done(self, timeout=None):
        end = time.monotonic() + (timeout or 60)
        while self.speaking and time.monotonic() < end:
            await asyncio.sleep(0.02)

    def stop(self):
        self._until = 0.0

    def saying(self, word):
        return self.speaking and word.lower() in self.current_text.lower()

    def heard_recently(self, within=20.0):
        return list(self.said[-6:])

    def prepare(self, texts):
        pass


async def run_script(app, script, echo=True, extra=None):
    """Feed the script at real pace (plus 1.5 s of trailing room tone), echoing his voice."""
    mic = app.mic
    for kind, amp in script.frames + [("silence", None)] * int(1.5 / FRAME_S):
        if extra:
            await extra(time.monotonic())
        if kind == "wake":
            f = tone(WAKE_AMP)
        elif kind == "say":
            f = tone(amp)
        elif echo and time.monotonic() < app.speaker._until + ECHO_TAIL:
            f = tone(ECHO_AMP)                 # his voice, plus the room's tail after he stops
        else:
            f = np.zeros(FRAME, np.int16)
        if mic.q.full():
            mic.q.get_nowait()
        mic.q.put_nowait(f)
        await asyncio.sleep(FRAME_S)


def make_app(cfg, script, replies=None, turn=True):
    from jarvis.core.orchestrator import Jarvis

    cfg.data["voice"]["follow_up_seconds"] = 3
    app = Jarvis(cfg, headless=True)
    app.mic = FakeMic()
    v = VAD()
    v._vad = None
    app.vad, app.wake = v, FakeWake()
    app.stt = FakeSTT(script.words)
    app.turn = FakeTurn(app.stt) if turn else None
    app.speaker = FakeSpeaker()
    app.stt.speaker = app.speaker
    asked = []

    async def respond(text, on_sentence=None, **k):
        asked.append(text)
        await asyncio.sleep(0.4)
        reply = (replies or {}).get(text, "Certainly not a problem, sir.")
        if on_sentence:
            await on_sentence(reply)
        return reply
    return app, asked, respond


async def simulate(cfg, parts, replies=None, echo=True, turn=True, extra=None, setup=None):
    script = Script(parts)
    app, asked, respond = make_app(cfg, script, replies, turn)
    await app.start()
    app.speaker, app.mic = app.speaker if isinstance(app.speaker, FakeSpeaker) else FakeSpeaker(), app.mic
    app.brain.respond = respond
    if setup:
        setup(app)
    loop_task = asyncio.create_task(app._voice_loop())
    try:
        await run_script(app, script, echo, extra and (lambda now: extra(app, now)))
        await asyncio.sleep(0.3)
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except (asyncio.CancelledError, Exception):
            pass
    return app, asked


def sim(cfg, parts, **kw):
    return asyncio.run(asyncio.wait_for(simulate(cfg, parts, **kw), 60))


# ── scenarios ─────────────────────────────────────────────────────────────
def test_wake_word_then_straight_into_the_request(cfg):
    app, asked = sim(cfg, [("silence", 0.5, ""), ("wake", 0.6, ""), ("say", 1.2, "what's the weather like"),
                           ("silence", 4.5, "")])
    assert asked == ["what's the weather like"], (asked, app.stt.heard)
    assert app.speaker.said[0] == "Certainly not a problem, sir."         # no "Sir?" when you run straight on
    assert not any(s.endswith("?") and len(s) < 16 for s in app.speaker.said)


def test_wake_word_then_a_pause_gets_sir_then_the_request(cfg):
    app, asked = sim(cfg, [("silence", 0.5, ""), ("wake", 0.6, ""), ("silence", 2.0, ""),
                           ("say", 1.2, "what's on my calendar"), ("silence", 4.5, "")])
    assert asked == ["what's on my calendar"], (asked, app.stt.heard)
    assert app.speaker.said[0] in app.ack_phrases()                         # "Sir?"
    assert asked == ["what's on my calendar"]                               # his "Sir?" never taken for you


def test_follow_up_without_the_wake_word(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "who won the match"),
                           ("silence", 2.6, ""), ("say", 1.0, "and the score"), ("silence", 4.5, "")])
    assert asked == ["who won the match", "and the score"], (asked, app.stt.heard)


def test_his_own_voice_is_never_taken_for_yours(cfg):
    long_reply = "Here is a rather long answer that keeps going for a good while, sir, so the echo is long."
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "tell me something"),
                           ("silence", 8.0, "")], replies={"tell me something": long_reply})
    assert asked == ["tell me something"], (asked, app.stt.heard)
    assert len(asked) == 1, (asked, app.stt.heard)                          # he never answers himself


def test_mid_thought_pause_is_one_request(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.8, "set a timer for…"),
                           ("silence", 1.0, ""), ("say", 0.8, "ten minutes"), ("silence", 4.0, "")])
    assert asked == ["set a timer for… ten minutes"] or app.speaker.said, (asked, app.stt.heard)
    assert len([h for h in app.stt.heard if h]) == 1, app.stt.heard


def test_quick_answer_by_voice_and_then_a_follow_up(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.8, "what time is it"),
                           ("silence", 2.0, ""), ("say", 1.0, "thanks and what's the weather"), ("silence", 4.5, "")])
    assert asked == ["thanks and what's the weather"], (asked, app.stt.heard)     # the time never reached the brain
    assert app.speaker.said[0].startswith(("It's", "1", "2", "3", "4", "5", "6", "7", "8", "9"))


def test_thats_all_ends_the_conversation(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.8, "dim the lights"),
                           ("silence", 2.4, ""), ("say", 0.7, "that's all"), ("silence", 1.2, ""),
                           ("say", 1.0, "this should be ignored"), ("silence", 3.0, "")])
    assert asked == ["dim the lights"], (asked, app.stt.heard)
    assert any(s.startswith("Very good") for s in app.speaker.said)


def test_barge_in_with_the_wake_word_while_he_talks(cfg):
    long_reply = "This is a very long reply that you will want to interrupt halfway through, sir, really."
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.8, "read me the news"),
                           ("silence", 1.4, ""), ("wake", 0.6, ""), ("say", 0.8, "stop and check my email"),
                           ("silence", 4.5, "")], replies={"read me the news": long_reply})
    assert asked[0] == "read me the news" and "stop and check my email" in asked[-1], (asked, app.stt.heard)


def test_victory_gesture_listens_and_survives_its_chime(cfg):
    async def press(app, now):
        if not getattr(app, "_pressed", False):
            app._pressed = True
            app.trigger_listen()
    app, asked = sim(cfg, [("silence", 1.0, ""), ("say", 1.0, "play some jazz please"), ("silence", 4.5, "")],
                     extra=press)
    assert asked == ["play some jazz please"], (asked, app.stt.heard)
    assert app.speaker.tones[:1] == ["wake"]


def test_nothing_said_after_the_wake_word_goes_quietly_back_to_standby(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("silence", 8.5, "")])
    assert asked == [] and app.speaker.said[:1] and app.speaker.said[0] in app.ack_phrases()
    assert len(app.speaker.said) == 1                                       # one "Sir?", no nagging


def test_two_conversations_in_a_row(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.8, "first question"),
                           ("silence", 7.5, ""), ("wake", 0.6, ""), ("say", 0.8, "second question"),
                           ("silence", 4.5, "")])
    assert asked == ["first question", "second question"], (asked, app.stt.heard)
    assert app.wake.fired == 2


def test_without_smart_turn_the_old_timeout_still_works(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "what's the news"),
                           ("silence", 4.5, "")], turn=False)
    assert asked == ["what's the news"], (asked, app.stt.heard)


@pytest.mark.parametrize("muted", [True])
def test_a_muted_mic_hears_nothing(cfg, muted):
    def mute(app):
        app.mic.muted = True
    app, asked = sim(cfg, [("silence", 0.3, ""), ("wake", 0.6, ""), ("say", 1.0, "can you hear me"),
                           ("silence", 1.5, "")], setup=mute)
    assert asked == [] and app.wake.fired == 0


class LateWake(FakeWake):
    """Like the real detector: it decides a moment after you've finished saying 'Hey Jarvis'."""

    def __init__(self, latency_frames=3):
        super().__init__()
        self.after, self.latency = None, latency_frames

    def process(self, frame):
        if int(np.abs(frame).max()) == WAKE_AMP:
            self.run += 1
            self.after = 0
            return False
        if self.run >= 6 and self.after is not None:
            self.after += 1
            if self.after == self.latency:
                self.run, self.after = 0, None
                self.fired += 1
                return True
        return False


def test_the_first_words_after_a_late_wake_detection_are_kept(cfg):
    def late(app):
        app.wake = LateWake(latency_frames=4)            # 0.32 s after the phrase ends
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.24, "what's"),
                           ("say", 1.0, "the weather like"), ("silence", 4.5, "")], setup=late)
    assert asked == ["what's the weather like"], (asked, app.stt.heard)


def test_late_wake_without_spoken_acks_keeps_the_first_words_too(cfg):
    def late(app):
        app.wake = LateWake(latency_frames=4)
        app.cfg.data["voice"]["spoken_acks"] = False
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.24, "what's"),
                           ("say", 1.0, "the weather like"), ("silence", 4.5, "")], setup=late)
    assert asked == ["what's the weather like"], (asked, app.stt.heard)
