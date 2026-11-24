"""Echo cancellation (Apple voice processing) and the fallbacks around it.

The engine itself needs a Mac with a microphone (the in-app self-test checks what it can there).
Here: the audio maths it relies on, how the speaker and microphone route through it, the watchdog
that falls back to plain audio, and the echo filter's fuzzy and timing rules."""
import asyncio
import threading
import time

import numpy as np

from jarvis.voice.apple_io import FRAME, Framer, StreamResampler, to_play_rate
from jarvis.voice.echo import strip_echo


# ── the audio maths ─────────────────────────────────────────────────────
def tone(freq, sr, secs):
    t = np.arange(int(sr * secs)) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def dominant(x, sr):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / sr)[int(np.argmax(spec))]


def test_resampler_keeps_pitch_and_length_across_blocks():
    for sr_in in (48000, 44100, 24000, 16000):
        r = StreamResampler(sr_in)
        x = tone(440, sr_in, 2.0)
        out = np.concatenate([r.process(x[i:i + 1024]) for i in range(0, len(x), 1024)])   # tap-sized blocks
        assert abs(len(out) - 32000) <= 2, (sr_in, len(out))                               # no drift
        assert abs(dominant(out[4000:], 16000) - 440) < 3, sr_in
        assert np.max(np.abs(np.diff(out[4000:]))) < 0.2, sr_in                            # no clicks at block edges


def test_resampler_removes_what_16k_cant_hold():
    r = StreamResampler(48000)
    out = r.process(tone(12000, 48000, 1.0))        # above 8 kHz: must not fold back down as a fake tone
    assert np.sqrt(np.mean(out[2000:] ** 2)) < 0.02


def test_framer_makes_80ms_int16_frames():
    f = Framer()
    frames = f.push(np.full(FRAME * 2 + 100, 0.25, np.float32))
    assert len(frames) == 2 and frames[0].dtype == np.int16 and len(frames[0]) == FRAME
    assert frames[0][0] == int(0.25 * 32767)
    assert len(f.push(np.zeros(FRAME - 100, np.float32))) == 1                             # leftovers carried


def test_speech_is_resampled_for_the_player():
    assert len(to_play_rate(np.zeros(22050, np.float32), 22050)) == 24000
    x = np.ones(100, np.float32)
    assert to_play_rate(x, 24000) is not None and len(to_play_rate(x, 24000)) == 100


# ── routing through the engine ──────────────────────────────────────────
class FakeVoiceIO:
    error = "device went away"

    def __init__(self):
        self.running, self.played, self.listeners, self.last_frame_at = True, [], [], time.monotonic()

    def add_listener(self, fn):
        self.listeners.append(fn)

    def play(self, audio, sr, should_stop, on_level=None):
        self.played.append((len(audio), sr))
        if on_level:
            on_level(0.1)

    def frames_flowing(self, within=3.0):
        return self.running and time.monotonic() - self.last_frame_at < within

    def stop(self):
        self.running = False

    def start(self):
        return False


def test_his_voice_and_chimes_play_through_the_echo_canceller():
    from jarvis.voice.tts import SilentTTS, Speaker

    sp = Speaker(SilentTTS())
    sp.voiceio = FakeVoiceIO()
    sp.start()
    sp.enqueue("Good evening, sir.")
    sp._idle.wait(5)
    assert sp.voiceio.played and sp.voiceio.played[0][1] == SilentTTS.sample_rate
    sp.play_tone("wake")
    for _ in range(50):
        if len(sp.voiceio.played) >= 2:
            break
        time.sleep(0.02)
    assert len(sp.voiceio.played) == 2                      # the chime too: the mic never hears it
    sp.close()


def test_the_microphone_takes_its_frames_from_the_echo_canceller():
    from jarvis.voice.audio import Microphone

    async def go():
        vio = FakeVoiceIO()
        mic = Microphone(None, voiceio=vio)
        q = mic.subscribe()
        mic.start(asyncio.get_running_loop())
        assert mic.echo_cancelled and vio.listeners
        threading.Thread(target=lambda: vio.listeners[0](np.ones(FRAME, np.int16))).start()
        frame = await asyncio.wait_for(q.get(), 2)
        mic.muted = True
        vio.listeners[0](np.ones(FRAME, np.int16))
        await asyncio.sleep(0.05)
        return len(frame), q.qsize()
    assert asyncio.run(go()) == (FRAME, 0)                  # muted means nothing reaches JARVIS


def test_if_echo_cancelled_audio_stops_he_falls_back_instead_of_going_deaf(cfg, monkeypatch):
    from jarvis.core.orchestrator import Jarvis
    from jarvis.voice.audio import Microphone

    started = []
    monkeypatch.setattr(Microphone, "start_plain", lambda self: started.append(True))

    async def go():
        app = Jarvis(cfg, headless=True)
        vio = FakeVoiceIO()
        vio.last_frame_at = 0.0                             # nothing for a while: a device went away
        app.voiceio = vio
        app.mic = Microphone(None, voiceio=vio)
        app.mic._loop = asyncio.get_running_loop()
        from jarvis.voice.tts import SilentTTS, Speaker
        app.speaker = Speaker(SilentTTS())
        app.speaker.voiceio = vio
        await asyncio.wait_for(app._watch_echo_cancellation(), 10)
        return app
    app = asyncio.run(go())
    assert started and app.voiceio is None and app.speaker.voiceio is None
    assert app.capabilities["echo_cancellation"] is False


def test_off_the_mac_or_switched_off_there_is_no_engine(cfg):
    from jarvis.core.orchestrator import Jarvis

    cfg.data["voice"]["echo_cancellation"] = False
    app = Jarvis(cfg, headless=True)
    assert app._start_echo_cancellation() is None and app.capabilities["echo_cancellation"] is False


# ── the echo filter ─────────────────────────────────────────────────────
SAID = ["The board meets at 10 tomorrow in the main conference room.", "Shall I send it to Pepper?"]


def test_misheard_echo_is_still_his():
    assert strip_echo("the board meets at ten tomorrow in the main conference room", SAID) == ""
    assert strip_echo("board meet at 10 tomorrow in main conference", SAID) == ""
    assert strip_echo("send this to pepper", SAID) == ""


def test_misheard_echo_then_you():
    assert strip_echo("send it to peppers? Yes and copy Happy", SAID) == "Yes and copy Happy"


def test_your_own_words_are_left_alone():
    assert strip_echo("what's the weather in London", SAID) == "what's the weather in London"
    assert strip_echo("send it", SAID) == "send it"
    assert strip_echo("no, move the board meeting to Friday", SAID) == "no, move the board meeting to Friday"


def test_only_audio_heard_right_after_him_is_filtered(cfg):
    from types import SimpleNamespace

    from jarvis.core.orchestrator import Jarvis

    j = Jarvis(cfg, headless=True)
    now = time.monotonic()
    j.speaker = SimpleNamespace(heard_recently=lambda: SAID, quiet_since=now - 5.0, speaking=False)
    assert j._without_echo("send it to Pepper", began=now - 1.0) == "send it to Pepper"   # you, seconds later
    assert j._without_echo("send it to Pepper", began=now - 4.5) == ""                     # just after him
    j.speaker.speaking = True
    assert j._without_echo("send it to Pepper", began=now - 1.0) == ""                     # while he talks


def test_speech_upsampled_for_the_player_keeps_its_pitch_and_timing():
    out = to_play_rate(tone(440, 22050, 1.0), 22050)
    assert abs(len(out) - 24000) <= 1 and abs(dominant(out[2000:-2000], 24000) - 440) < 3
    assert np.argmax(np.abs(out) > 0.1) < 50                    # no added delay at the start


class FakePlayer:
    """Stands in for AVAudioPlayerNode: 'plays' scheduled buffers in real time on a thread."""

    def __init__(self):
        self.scheduled, self.playing, self.stops = [], False, 0

    def scheduleBuffer_completionHandler_(self, buf, done):
        self.scheduled.append(len(buf))
        if done is not None:
            total = sum(self.scheduled) / 24000

            import threading
            threading.Timer(min(total, 0.3), done).start()

    def isPlaying(self):
        return self.playing

    def play(self):
        self.playing = True

    def stop(self):
        self.stops += 1


def _engine(monkeypatch):
    import threading

    from jarvis.voice import apple_io

    monkeypatch.setattr(apple_io, "make_buffer", lambda fmt, a: np.asarray(a))
    vio = apple_io.VoiceIO.__new__(apple_io.VoiceIO)
    vio.player, vio.play_format, vio._play_lock, vio._gen = FakePlayer(), object(), threading.Lock(), 0
    return vio


def test_streamed_speech_is_scheduled_as_it_arrives_and_completes(monkeypatch):
    vio = _engine(monkeypatch)
    chunks = [np.full(4800, 0.2, np.float32), None, np.full(4800, 0.2, np.float32), np.full(2400, 0.2, np.float32)]
    levels = []
    vio.play_stream(iter(chunks), 24000, should_stop=lambda: False, on_level=levels.append, tail=0.1)
    assert sum(vio.player.scheduled) == 4800 * 2 + 2400 + 2400          # every sample, plus the breath after
    assert vio.player.playing and vio.player.stops == 0


def test_a_stop_while_streaming_drops_the_rest(monkeypatch):
    vio = _engine(monkeypatch)
    stop = {"now": False}

    def chunks():
        yield np.full(12000, 0.2, np.float32)
        stop["now"] = True
        yield np.full(12000, 0.2, np.float32)
    vio.play_stream(chunks(), 24000, should_stop=lambda: stop["now"])
    assert vio.player.stops == 1 and sum(vio.player.scheduled) == 12000
