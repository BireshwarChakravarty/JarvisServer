"""Echo cancellation: the microphone and his voice through one Apple voice-processing engine.

Without it, the microphone hears JARVIS through the speakers, and everything downstream has to
guess which sound is his: the wake word fires on his own voice, the end of his sentence is
transcribed as you speaking, and he answers himself. Guessing by timing breaks on Bluetooth
speakers, in echoey rooms, or when you talk while he does.

macOS has the same echo canceller FaceTime uses: an AVAudioEngine whose input node has voice
processing turned on. It knows exactly what the engine is playing, so it subtracts his voice from
the microphone before anything listens. That only works for sound played through the same engine,
so his voice and chimes play through it too (an AVAudioPlayerNode), and the microphone frames come
from a tap on its input node, resampled to 16 kHz and cut into 80 ms frames like before.

If the engine can't start (no input device, a device that voice processing can't drive, an
unusual Mac), JARVIS falls back to the plain sounddevice path and says so in the log and the
self-test. Nothing else changes: frames still reach asyncio queues, speech still plays in phrases.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import numpy as np

log = logging.getLogger("jarvis.voiceio")

SAMPLE_RATE = 16000
FRAME = 1280
PLAY_RATE = 24000                    # what the player node takes; other rates are resampled first
CHUNK_S = 0.1                        # his speech is scheduled in 100 ms buffers, so a stop is quick


# ── pure numpy helpers (unit-tested on any platform) ─────────────────────
def lowpass(n: int, cutoff: float, fs: float) -> np.ndarray:
    """A windowed-sinc low-pass FIR (Blackman window, unity gain at DC)."""
    m = np.arange(n) - (n - 1) / 2
    h = np.sinc(2 * cutoff / fs * m) * np.blackman(n)
    return (h / h.sum()).astype(np.float64)


class StreamResampler:
    """Streaming resampler for the microphone: anti-alias low-pass (state kept across blocks), then
    linear interpolation at a continuous phase, so block edges never click or drift."""

    def __init__(self, sr_in: float, sr_out: float = SAMPLE_RATE) -> None:
        self.sr_in, self.sr_out = float(sr_in), float(sr_out)
        self.ratio = self.sr_in / self.sr_out
        self.passthrough = abs(self.ratio - 1.0) < 1e-6
        if not self.passthrough:
            self.taps = lowpass(95, 0.45 * min(self.sr_in, self.sr_out), self.sr_in)
            self.hist = np.zeros(len(self.taps) - 1)
        self.pos = 0.0                # where the next output sample falls, in input samples of this block
        self.prev = 0.0               # the previous block's last filtered sample

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64).reshape(-1)
        if self.passthrough or not len(x):
            return x.astype(np.float32)
        buf = np.concatenate([self.hist, x])
        y = np.convolve(buf, self.taps, mode="valid")        # FIR with the previous block's tail
        self.hist = buf[-(len(self.taps) - 1):]
        n = len(y)
        ext = np.concatenate([[self.prev], y])               # ext[k + 1] == y[k]
        self.prev = float(y[-1])
        if self.pos > n - 1:
            self.pos -= n
            return np.zeros(0, np.float32)
        ps = np.arange(self.pos, n - 1 + 1e-9, self.ratio)
        out = np.interp(ps + 1.0, np.arange(n + 1), ext)
        self.pos = float(ps[-1] + self.ratio - n)
        return out.astype(np.float32)


class Framer:
    """16 kHz float samples in → 80 ms int16 frames out (what the wake word and VAD expect)."""

    def __init__(self, frame: int = FRAME) -> None:
        self.frame = frame
        self.buf = np.zeros(0, np.float32)

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        self.buf = np.concatenate([self.buf, samples.astype(np.float32)])
        out = []
        while len(self.buf) >= self.frame:
            chunk, self.buf = self.buf[:self.frame], self.buf[self.frame:]
            out.append((np.clip(chunk, -1.0, 1.0) * 32767).astype(np.int16))
        return out


def to_play_rate(audio: np.ndarray, sr: int, target: int = PLAY_RATE) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if int(sr) == int(target) or not len(audio):
        return audio
    r = StreamResampler(sr, target)
    pad = np.zeros(len(r.taps) // 2 if not r.passthrough else 0, np.float32)
    out = r.process(np.concatenate([audio, pad]))           # the filter's delay, flushed with silence
    delay = int(round((len(r.taps) - 1) / 2 / r.ratio)) if not r.passthrough else 0
    want = int(round(len(audio) * target / sr))
    return out[delay:delay + want].astype(np.float32)


# ── PyObjC buffer access ─────────────────────────────────────────────────
def channel(buffer, n: int, index: int = 0) -> np.ndarray:
    """A numpy view of one channel of an AVAudioPCMBuffer (float32). Writable for buffers we own."""
    data = buffer.floatChannelData()
    ch = data[index]
    try:
        mv = ch.as_buffer(n)
    except TypeError:
        mv = ch.as_buffer(n * 4)
    arr = np.frombuffer(mv, dtype=np.float32)
    return arr[:n]


def make_buffer(fmt, audio: np.ndarray):
    """An AVAudioPCMBuffer holding `audio` (mono float32) in format `fmt`."""
    import AVFoundation as AV

    n = len(audio)
    buf = AV.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(fmt, max(1, n))
    buf.setFrameLength_(n)
    if n:
        view = channel(buf, n)
        if not view.flags.writeable:
            view = np.asarray(memoryview(buf.floatChannelData()[0].as_buffer(n)).cast("B").cast("f"))
        view[:] = audio
    return buf


# ── the engine ────────────────────────────────────────────────────────────
class VoiceIO:
    """Mic in and voice out through one voice-processing AVAudioEngine (echo cancelled)."""

    def __init__(self) -> None:
        self.engine = None
        self.player = None
        self.play_format = None
        self.error = ""
        self.running = False
        self.in_rate = 0.0
        self.last_frame_at = 0.0
        self._listeners: list[Callable[[np.ndarray], None]] = []
        self._resampler: StreamResampler | None = None
        self._framer = Framer()
        self._play_lock = threading.Lock()
        self._gen = 0

    # mic
    def add_listener(self, fn: Callable[[np.ndarray], None]) -> None:
        self._listeners.append(fn)

    def start(self) -> bool:
        try:
            import AVFoundation as AV

            engine = AV.AVAudioEngine.alloc().init()
            inp = engine.inputNode()
            res = inp.setVoiceProcessingEnabled_error_(True, None)
            ok = res[0] if isinstance(res, tuple) else bool(res)
            if not ok:
                raise RuntimeError(f"voice processing refused: {res[1] if isinstance(res, tuple) else res}")
            try:   # no automatic gain: it lifts quiet room sound (a TV, the next room) up to your level
                inp.setVoiceProcessingAGCEnabled_(False)
            except Exception:
                pass
            try:   # don't turn your music right down while he listens (the default ducks it hard)
                inp.setVoiceProcessingOtherAudioDuckingConfiguration_((False, 10))
            except Exception:
                pass
            fmt = inp.outputFormatForBus_(0)
            self.in_rate = float(fmt.sampleRate())
            if self.in_rate <= 0:
                raise RuntimeError("the input device reports no sample rate")
            self._resampler = StreamResampler(self.in_rate)

            def tap(buffer, when):
                try:
                    n = int(buffer.frameLength())
                    if n <= 0:
                        return
                    mono = np.array(channel(buffer, n), copy=True)
                    for frame in self._framer.push(self._resampler.process(mono)):
                        self.last_frame_at = time.monotonic()
                        for fn in list(self._listeners):
                            fn(frame)
                except Exception:          # never raise on the audio thread
                    log.exception("voice-processing tap failed")

            inp.installTapOnBus_bufferSize_format_block_(0, 1024, fmt, tap)
            player = AV.AVAudioPlayerNode.alloc().init()
            engine.attachNode_(player)
            self.play_format = AV.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(PLAY_RATE, 1)
            engine.connect_to_format_(player, engine.mainMixerNode(), self.play_format)
            engine.prepare()
            res = engine.startAndReturnError_(None)
            ok = res[0] if isinstance(res, tuple) else bool(res)
            if not ok:
                raise RuntimeError(f"the audio engine wouldn't start: {res[1] if isinstance(res, tuple) else res}")
            player.play()
            self.engine, self.player, self.running = engine, player, True
            log.info("echo cancellation on: voice processing at %.0f Hz", self.in_rate)
            return True
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("echo cancellation unavailable (%s); using plain audio", self.error)
            self.stop()
            return False

    def frames_flowing(self, within: float = 3.0) -> bool:
        return self.running and time.monotonic() - self.last_frame_at < within

    def stop(self) -> None:
        eng, self.engine = self.engine, None
        self.running = False
        if eng is not None:
            try:
                eng.inputNode().removeTapOnBus_(0)
            except Exception:
                pass
            try:
                eng.stop()
            except Exception:
                pass
        self.player = None

    # voice out
    def play(self, audio: np.ndarray, sr: int, should_stop: Callable[[], bool],
             on_level: Callable[[float], None] | None = None) -> None:
        """Play mono float audio and return when it has been heard (or should_stop() says so)."""
        player, fmt = self.player, self.play_format
        if player is None or fmt is None:
            raise RuntimeError("voice engine not running")
        audio = to_play_rate(audio, sr)
        if not len(audio):
            return
        step = int(PLAY_RATE * CHUNK_S)
        done = threading.Event()
        with self._play_lock:
            self._gen += 1
            starts = range(0, len(audio), step)
            last = len(starts) - 1
            for i, s in enumerate(starts):
                buf = make_buffer(fmt, audio[s:s + step])
                if i == last:
                    player.scheduleBuffer_completionHandler_(buf, done.set)
                else:
                    player.scheduleBuffer_completionHandler_(buf, None)
            if not player.isPlaying():
                player.play()
            t0 = time.monotonic()
            dur = len(audio) / PLAY_RATE
            while not done.is_set():
                if should_stop():
                    player.stop()          # drops everything scheduled, at once
                    player.play()          # ready for the next phrase
                    return
                if on_level is not None:
                    i = min(len(audio) - 1, int((time.monotonic() - t0) * PLAY_RATE))
                    seg = audio[i:i + int(PLAY_RATE * 0.04)]
                    on_level(float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0)
                done.wait(0.04)
                if time.monotonic() - t0 > dur + 3.0:    # a completion that never came: don't hang
                    log.warning("voice-processing playback didn't report completion")
                    break
