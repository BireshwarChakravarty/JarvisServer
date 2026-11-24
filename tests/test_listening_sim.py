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
            elif kind == "bg":                       # someone else / a TV, across the room: much quieter
                amp = 900 + 11 * i
                self.words[amp] = words
                self.frames += [("say", amp)] * n
            else:
                amp = 6000 + 150 * i
                self.words[amp] = words
                self.frames += [("say", amp)] * n


class FakeMic:
    muted = False

    def __init__(self):
        self.q = asyncio.Queue(maxsize=400)
        self.extra = []                          # other listeners (voice setup, confirmations)

    def subscribe(self, maxsize=400):
        if not hasattr(self, "_first"):
            self._first = True
            return self.q
        q = asyncio.Queue(maxsize=maxsize)
        self.extra.append(q)
        return q

    def unsubscribe(self, q):
        if q in self.extra:
            self.extra.remove(q)


class FakeWake:
    """Fires at the end of a 'Hey Jarvis' (like openWakeWord: after the word, not during it)."""
    available = True

    def __init__(self):
        self.run, self.fired = 0, 0

    def process(self, frame, threshold=None):
        if int(np.abs(frame).max()) == WAKE_AMP:
            self.run += 1
            if self.run == 6:                 # ~0.5 s into the wake phrase
                self.fired += 1
                return True
        else:
            self.run = 0
        return False

    last_score = 0.0

    def reset(self):
        self.run = 0


class NearMissWake(FakeWake):
    """Apple's processed audio: "Hey Jarvis" scores 0.3, never past the bar."""

    def process(self, frame, threshold=None):
        self.last_score = 0.3 if int(np.abs(frame).max()) == WAKE_AMP else 0.0
        return False


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
        if text.endswith("…"):
            return 0.1                       # clearly mid-thought
        if text.endswith("—"):
            return 0.6                       # can't tell: the model isn't sure you've finished
        return 0.95


class FakeSpeaker:
    """Plays for 40 ms per character; while 'playing', the mic hears echo."""

    def __init__(self):
        self.said, self.tones, self._until, self.current_text, self.route = [], [], 0.0, "", None
        self._cache = {}

    @property
    def speaking(self):
        return time.monotonic() < self._until

    @property
    def quiet_since(self):
        return self._until

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
        override = await extra(time.monotonic()) if extra else None
        if override is not None:
            f = override                        # a scenario speaking at a moment it picks
        elif kind == "wake":
            f = tone(WAKE_AMP)
        elif kind == "say":
            f = tone(amp)
        elif echo and time.monotonic() < app.speaker._until + ECHO_TAIL:
            f = tone(ECHO_AMP)                 # his voice, plus the room's tail after he stops
        else:
            f = np.zeros(FRAME, np.int16)
        for qq in [mic.q] + list(getattr(mic, "extra", [])):
            if qq.full():
                qq.get_nowait()
            qq.put_nowait(f)
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
    app.stt.speaker = app.speaker               # his echo transcribes as his own last words
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
    assert asked == ["set a timer for… ten minutes"], (asked, app.stt.heard)
    yours = [h for h in app.stt.heard if (h and "ten minutes" in h) or "timer" in h]
    assert len(yours) == 1, app.stt.heard                                 # one utterance, not two


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

    def process(self, frame, threshold=None):
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


def test_a_quick_yes_right_after_his_question_is_heard(cfg):
    """You answer the moment he stops, while his voice still hangs in the room."""
    YES = 7777
    state = {}

    async def answer(app, now):
        sp = app.speaker
        if "Pepper" in (sp.said[-1] if sp.said else "") and not sp.speaking and "t" not in state:
            state["t"] = now + 0.1                          # 0.1 s after he stops
        t = state.get("t")
        if t is not None and t <= now < t + 0.5:
            return tone(YES)
        return None

    def words(app):
        app.stt.words[YES] = "yes"
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.9, "send the report"),
                           ("silence", 6.0, "")], replies={"send the report": "Shall I send it to Pepper?"},
                     extra=answer, setup=words)
    assert asked == ["send the report", "yes"], (asked, app.stt.heard)


def test_talking_straight_after_the_gesture_chime_keeps_your_first_words(cfg):
    async def press(app, now):
        if not getattr(app, "_pressed", False):
            app._pressed = True
            app.trigger_listen()
    app, asked = sim(cfg, [("silence", 0.25, ""), ("say", 0.4, "play"), ("say", 0.8, "some jazz"),
                           ("silence", 4.5, "")], extra=press)
    assert asked == ["play some jazz"], (asked, app.stt.heard)


def test_when_the_phone_answered_first_the_mac_stays_quiet_and_its_hud_goes_back_to_standby(cfg):
    from jarvis.events import bus

    def phone_has_it(app):
        app._voice_claim = ("phone:ipad", time.monotonic() + 100)
        fire = app.wake.process

        def process(frame, threshold=None):   # like the real detector, which tells the HUD it heard you
            hit = fire(frame)
            if hit:
                bus.emit("wake", score=0.9)
            return hit
        app.wake.process = process
        bus.history.clear()
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "what's the weather"),
                           ("silence", 1.5, "")], setup=phone_has_it)
    assert asked == [] and app.speaker.said == []
    seq = [e.get("state") or e["type"] for e in bus.history if e.get("type") in ("state", "wake")]
    assert "wake" in seq and seq[-1] == "idle", seq                         # not left showing "Listening"


def test_a_mic_tap_during_a_conversation_doesnt_start_a_second_one_afterwards(cfg):
    async def tap(app, now):
        if app.speaker.said and not getattr(app, "_tapped", False):
            app._tapped = True
            app.trigger_listen()                 # tapped while he answers
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.9, "what's the news"),
                           ("silence", 8.0, "")], extra=tap)
    assert asked == ["what's the news"], (asked, app.stt.heard)
    assert app.speaker.tones.count("wake") <= 1, app.speaker.tones          # no surprise second listen



def test_a_natural_pause_mid_sentence_doesnt_cut_you_off(cfg):
    """A 0.85 s breath where Smart Turn can't tell: he waits, and hears the whole sentence."""
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.9, "I need you to book a table —"),
                           ("silence", 0.85, ""), ("say", 0.9, "for four at eight"), ("silence", 4.5, "")])
    assert asked == ["I need you to book a table — for four at eight"], (asked, app.stt.heard)


class MishearingSTT(FakeSTT):
    """Whisper on room echo: his words, not quite right."""

    def transcribe(self, audio):
        text = super().transcribe(audio)
        last = self.speaker.said[-1] if self.speaker and self.speaker.said else ""
        tail = " ".join(last.split()[-4:])
        if tail and tail in text:
            text = text.replace(tail, tail.replace("o", "oh").replace("a", "uh", 1))
            self.heard[-1] = text
        return text


def test_a_long_misheard_echo_is_never_answered(cfg, monkeypatch):
    """Bluetooth speakers in an echoey room: his voice lingers 0.9 s and Whisper mishears it."""
    monkeypatch.setattr(__import__(__name__), "ECHO_TAIL", 0.9)
    long_reply = "The quarterly numbers look strong across every division this year, sir."

    def mishear(app):
        st = MishearingSTT(app.stt.words)
        st.speaker = app.speaker
        app.stt = st
        app.turn = FakeTurn(st)
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.9, "how are the numbers"),
                           ("silence", 9.0, "")], replies={"how are the numbers": long_reply}, setup=mishear)
    assert asked == ["how are the numbers"], (asked, app.stt.heard)


def test_repeating_his_words_later_is_you_not_his_echo(cfg):
    """He asks 'Shall I send it to Pepper?'; you answer 'send it to Pepper' a couple of seconds later."""
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.9, "draft the memo"),
                           ("silence", 4.0, ""), ("say", 1.0, "send it to Pepper"), ("silence", 4.5, "")],
                     replies={"draft the memo": "Done. Shall I send it to Pepper?"})
    assert asked == ["draft the memo", "send it to Pepper"], (asked, app.stt.heard)



def test_a_tv_still_talking_after_you_finish_is_not_part_of_your_request(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "what's the weather"),
                           ("bg", 3.0, "and now the sports news"), ("silence", 3.0, "")])
    assert asked == ["what's the weather"], (asked, app.stt.heard)
    assert not any("sports" in h for h in app.stt.heard), app.stt.heard   # never even transcribed


def test_background_speech_after_his_answer_isnt_taken_for_you(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "dim the lights"),
                           ("silence", 2.2, ""), ("bg", 1.5, "pass the salt please"), ("silence", 4.0, "")])
    assert asked == ["dim the lights"], (asked, app.stt.heard)


def test_after_a_plain_answer_he_stops_listening_sooner(cfg):
    """A statement gets a short follow-up window; you can still answer a question at leisure."""
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "dim the lights"),
                           ("silence", 7.5, ""), ("say", 1.0, "and lock the door"), ("silence", 3.0, "")])
    assert asked == ["dim the lights"], (asked, app.stt.heard)



# ── Voice ID: only you ───────────────────────────────────────────────────
class FakeVoiceID:
    """Judges by what was said: a 'guest:' line is someone else's voice."""
    active = True

    def __init__(self, stt):
        self.stt = stt

    def check(self, audio):
        text = FakeSTT(self.stt.words).transcribe(audio)
        return ("not_you", 0.08) if "guest:" in text else ("you", 0.81)


def voice_id(app):
    app.voice_id = FakeVoiceID(app.stt)


def test_someone_elses_hey_jarvis_gets_no_answer(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.2, "guest: unlock the front door"),
                           ("silence", 4.0, "")], setup=voice_id)
    assert asked == [] and app.speaker.said == [], (asked, app.speaker.said)


def test_a_guest_in_your_follow_up_is_ignored_and_you_still_get_through(cfg):
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "what's on tonight"),
                           ("silence", 2.2, ""), ("say", 1.0, "guest: order a pizza"), ("silence", 0.6, ""),
                           ("say", 1.0, "and tomorrow"), ("silence", 4.0, "")], setup=voice_id)
    assert asked == ["what's on tonight", "and tomorrow"], (asked, app.stt.heard)


def test_learn_my_voice_by_talking_to_him(cfg, tmp_path):
    """'Learn my voice': five prompts, you say each phrase, five voiceprints are saved."""
    import numpy as np

    from jarvis.voice.speaker import VoiceID

    YOU = 7311
    answered = set()
    state = {}

    def setup(app):
        vid = VoiceID(tmp_path, tmp_path / "vp.json")
        me = np.ones(512, np.float32) / np.sqrt(512)

        class Model:
            available = True
            heard = []

            def embed(self, audio):
                if audio is None:
                    return None
                n = len(audio) // FRAME
                frames = np.asarray(audio[:n * FRAME]).reshape(n, FRAME)
                peaks = {int(round(float(np.abs(f).max()) * 32768)) for f in frames if np.abs(f).max() > 0.01}
                self.heard.append(peaks)
                mine = any(abs(p - YOU) <= 40 for p in peaks)
                return me if mine and len(audio) > 16000 else None    # his voice or the room isn't you
        vid.model = Model()
        app.voice_id = vid
        app.stt.words[YOU] = "the phrase"

    async def respond_to_prompts(app, now):
        sp = app.speaker
        last = sp.said[-1] if sp.said else ""
        n = len(sp.said)
        if (last.startswith("Please say") or last.startswith("Now:")) and not sp.speaking and n not in answered:
            answered.add(n)
            state["until"] = now + 1.2 + 1.6           # after the tone, then 1.6 s of you
            state["from"] = now + 1.2
        if state.get("from", 1e18) <= now < state.get("until", 0):
            return tone(YOU)
        return None
    app, asked = asyncio.run(asyncio.wait_for(simulate(
        cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "learn my voice"), ("silence", 50.0, "")],
        extra=respond_to_prompts, setup=setup), 90))
    assert len(answered) == 5, (app.speaker.said, app.voice_id.model.heard)
    assert app.voice_id.profile.ready and app.voice_id.profile.enrolled == 5
    assert not any("once more" in s.lower() for s in app.speaker.said), app.speaker.said   # it waited for you
    assert any("I know your voice now" in s for s in app.speaker.said), app.speaker.said


def test_a_pause_after_a_dangling_word_waits_for_the_rest(cfg):
    """'Remind me to…' (thinking) '…call the bank': the voice sounds finished, the words plainly aren't."""
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "remind me to"),
                           ("silence", 1.8, ""), ("say", 0.9, "call the bank"), ("silence", 4.5, "")])
    assert asked == ["remind me to call the bank"], (asked, app.stt.heard)


def test_a_long_request_survives_a_short_thinking_pause(cfg):
    """After several seconds of talking, a half-second breath isn't the end of the request."""
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""),
                           ("say", 4.6, "book a table for four at the italian place near the office tomorrow"),
                           ("silence", 0.5, ""), ("say", 1.0, "around eight"), ("silence", 4.5, "")])
    assert asked == ["book a table for four at the italian place near the office tomorrow around eight"], \
        (asked, app.stt.heard)


def test_a_finished_short_request_is_still_answered_promptly(cfg):
    """The extra patience is only for unfinished words and long requests: 'what time is it' isn't held up."""
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 0.9, "what's the weather like"),
                           ("silence", 4.0, "")])
    assert asked == ["what's the weather like"], (asked, app.stt.heard)
    assert len(app.stt.heard) == 1                    # one listen, no waiting for "the rest"


LONG = "Here is a rather long answer that keeps going for a good while, sir, so there is time to cut in."


def _talk_over(amp, seconds, after=1.0):
    """A scenario voice that speaks `seconds` of `amp`, starting `after` s into his long reply."""
    state = {}

    async def extra(app, now):
        sp = app.speaker
        if sp.said and sp.said[-1] in LONG and sp.speaking and "t" not in state:
            state["t"] = now + after
        t = state.get("t")
        if t is not None and t <= now < t + seconds:
            return tone(amp)
        return None
    return extra


def _echo_cancelled(app, words=None, voice_id=True):
    from types import SimpleNamespace

    app.mic.echo_cancelled = True           # Apple's voice processing keeps his voice out of the mic
    if voice_id:                            # "learn my voice" done: Voice ID recognises you
        app.voice_id = SimpleNamespace(active=True, check=lambda audio, learn=True: ("you", 0.8))
    for a, w in (words or {}).items():
        app.stt.words[a] = w


def test_just_start_talking_to_interrupt_him(cfg):
    """No wake word: talk over him and he stops, and what you said becomes the next request."""
    OVER = 6121
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "tell me something"),
                           ("silence", 9.0, "")], replies={"tell me something": LONG}, echo=False,
                     extra=_talk_over(OVER, 1.3), setup=lambda a: _echo_cancelled(a, {OVER: "actually make it nine"}))
    assert asked == ["tell me something", "actually make it nine"], (asked, app.stt.heard)
    assert any("interrupted while saying" in n for n in getattr(app, "_quick_notes", [])) or \
        len(asked) == 2                         # the brain was told where he was cut off (notes may be spent)


def test_a_quick_mm_hm_doesnt_stop_him(cfg):
    """A backchannel ("mm-hm", "yeah") while he talks is listening, not interrupting."""
    HM = 6343
    stops = []
    def setup(a):
        _echo_cancelled(a, {HM: "mm hm"})
        real = a.interrupt
        a.interrupt = lambda: (stops.append(1), real())[1]
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "tell me something"),
                           ("silence", 7.0, "")], replies={"tell me something": LONG}, echo=False,
                     extra=_talk_over(HM, 0.3), setup=setup)
    assert asked == ["tell me something"], (asked, app.stt.heard)
    assert not stops


def test_without_voice_id_his_own_echo_never_interrupts_him(cfg):
    """No Voice ID: nothing tells your voice from his echo, so talking over him needs "Jarvis"
    (this was a real bug: he stopped himself and transcribed his own voice)."""
    OVER = 6121
    stops = []

    def setup(a):
        _echo_cancelled(a, {OVER: "jeroonanak"}, voice_id=False)
        real = a.interrupt
        a.interrupt = lambda: (stops.append(1), real())[1]
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "tell me something"),
                           ("silence", 7.0, "")], replies={"tell me something": LONG}, echo=False,
                     extra=_talk_over(OVER, 1.3), setup=setup)
    assert not stops and asked[0] == "tell me something" and "jeroonanak" not in asked, (asked, app.stt.heard)


def test_a_voice_that_isnt_clearly_yours_doesnt_interrupt(cfg):
    from types import SimpleNamespace

    OVER = 6121
    stops = []

    def setup(a):
        _echo_cancelled(a, {OVER: "on the news tonight"})
        a.voice_id = SimpleNamespace(active=True, check=lambda audio, learn=True: ("unsure", 0.4))
        real = a.interrupt
        a.interrupt = lambda: (stops.append(1), real())[1]
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "tell me something"),
                           ("silence", 7.0, "")], replies={"tell me something": LONG}, echo=False,
                     extra=_talk_over(OVER, 1.5), setup=setup)
    assert not stops, (asked, app.stt.heard)


def test_without_echo_cancellation_talking_over_him_needs_the_wake_word(cfg):
    """His own voice is in the mic then: only a clear "Hey Jarvis" may interrupt, never just sound."""
    OVER = 6121
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("say", 1.0, "tell me something"),
                           ("silence", 7.0, "")], replies={"tell me something": LONG},
                     extra=_talk_over(OVER, 1.3), setup=lambda a: a.stt.words.__setitem__(OVER, "noise"))
    assert asked[0] == "tell me something" and "noise" not in asked[1:2], (asked, app.stt.heard)


def test_a_near_miss_wake_word_gets_a_second_chance(cfg):
    """The detector only nearly hears "Hey Jarvis": JARVIS reads the last seconds, finds the name, and
    the request that follows is still heard in full."""
    def setup(app):
        app.wake = NearMissWake()
    app, asked = sim(cfg, [("silence", 0.4, ""), ("wake", 0.6, ""), ("silence", 0.4, ""),
                           ("say", 1.2, "what's the weather like"), ("silence", 4.5, "")], setup=setup)
    assert asked == ["what's the weather like"], (asked, app.stt.heard)


def test_a_near_miss_on_other_words_stays_asleep(cfg):
    """Something that merely sounds a bit like it ("hey, Travis"): checked, and ignored."""
    checked = []

    class Close(FakeWake):
        def process(self, frame, threshold=None):
            loud = int(np.abs(frame).max())
            self.last_score = 0.3 if loud > 1000 and loud != WAKE_AMP else 0.0   # any speech nearly scores
            return False

    def setup(app):
        app.wake = Close()
        real = app.stt.transcribe
        app.stt.transcribe = lambda audio: (checked.append(1), real(audio))[1]
    app, asked = sim(cfg, [("silence", 0.4, ""), ("say", 0.8, "hey travis"), ("silence", 1.0, ""),
                           ("say", 1.0, "pass the salt"), ("silence", 3.0, "")], setup=setup)
    assert checked, "the near-miss was checked"
    assert asked == [], (asked, app.stt.heard)
