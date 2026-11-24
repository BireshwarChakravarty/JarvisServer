"""The natural voice: optional download, Classic until it's ready and fast enough here, never stutters."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from jarvis.voice import qwen_tts as q


def test_the_voices_ship_with_the_app():
    for vid in q.HUMAN_VOICES:
        assert (q.VOICES_DIR / f"{vid}.wav").stat().st_size > 100_000, vid
    assert q.REF_TEXT.startswith("Good evening, sir.")


def test_the_download_is_optional_until_chosen(tmp_path):
    from jarvis.models import ModelManager

    mm = ModelManager(tmp_path)
    assert "human" not in [s["key"] for s in mm.status()]
    mm.want("human")
    row = next(s for s in mm.status() if s["key"] == "human")
    assert row["total"] > 1_900_000_000 and not row["ready"]


def test_a_natural_voice_starts_on_classic(tmp_path, monkeypatch):
    from jarvis.voice import tts

    seen = {}

    class FakeKokoro:
        name = "kokoro-onnx"

        def __init__(self, voice, speed, models_dir):
            seen["voice"] = voice
    monkeypatch.setattr(tts, "KokoroONNX", FakeKokoro)
    (tmp_path / "kokoro-v1.0.onnx").write_bytes(b"x")
    b = tts.make_backend("auto", "butler", 1.0, "auto", tmp_path)
    assert b.name == "kokoro-onnx" and seen["voice"] == "jarvis"


class FakeModel:
    sample_rate = 24000

    def __init__(self, rtf):
        self.rtf = rtf

    def generate(self, text, **kw):
        import time

        audio = np.zeros(int(24000 * 0.05 * len(text.split())), np.float32)
        time.sleep(len(audio) / 24000 * self.rtf)
        yield SimpleNamespace(audio=audio)



def test_timing_decides(monkeypatch, tmp_path):
    import sys
    import types

    fake = types.ModuleType("mlx_audio.tts.utils")
    for rtf, ok in ((0.3, True), (1.4, False)):
        fake.load_model = lambda d, rtf=rtf: FakeModel(rtf)
        monkeypatch.setitem(sys.modules, "mlx_audio", types.ModuleType("mlx_audio"))
        monkeypatch.setitem(sys.modules, "mlx_audio.tts", types.ModuleType("mlx_audio.tts"))
        monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", fake)
        nv = q.QwenVoice("butler", tmp_path, max_rtf=0.85)
        try:
            if ok:
                assert nv.load() < 0.85
                assert len(nv.synth("Done. The lights are off.")) > 1000
            else:
                with pytest.raises(q.TooSlow):
                    nv.load()
        finally:
            nv.close()


def _app(cfg, monkeypatch, speeds, force=False, voice="butler"):
    """A JARVIS whose natural voice measures `speeds` = {tier: real-time factor} on this 'Mac'."""
    from jarvis.core.orchestrator import Jarvis

    app = Jarvis(cfg, headless=True)
    tts = app.cfg.data.setdefault("voice", {}).setdefault("tts", {})
    tts.update(voice=voice, natural_settle_seconds=0, natural_force=force)
    app.speaker = SimpleNamespace(backend=SimpleNamespace(name="kokoro-onnx"), _cache={}, prepare=lambda t: None)
    monkeypatch.setattr(q, "supported", lambda: True)
    monkeypatch.setattr(q, "model_ready", lambda d, tier="large": True)
    said, loads = [], []

    async def announce(text, priority="normal"):
        said.append(text)
    app.announce = announce

    class Timed(q.QwenVoice):
        def load(self, measure=True):
            loads.append((self.tier, measure))
            self.rtf = speeds[self.tier]
            if self.rtf > self.max_rtf:
                raise q.TooSlow(f"{self.rtf:.2f}x real time")
            return self.rtf
    monkeypatch.setattr(q, "QwenVoice", Timed)
    return app, said, loads


def test_the_lighter_model_takes_over_when_the_larger_is_too_slow(cfg, monkeypatch):
    app, said, loads = _app(cfg, monkeypatch, {"large": 0.94, "small": 0.62})     # the real Mac's numbers
    asyncio.run(app.upgrade_voice())
    assert app.speaker.backend.name == "qwen3" and app.speaker.backend.tier == "small"
    assert "small model" in app.capabilities["natural_voice"] and not said
    app.speaker.backend.close()
    app.speaker.backend = SimpleNamespace(name="kokoro-onnx")
    asyncio.run(app.upgrade_voice())                   # next start: no re-timing, straight to the small one
    assert loads[-1] == ("small", False) and app.speaker.backend.tier == "small"
    app.speaker.backend.close()


def test_friday_speaks_only_through_the_lighter_model(cfg, monkeypatch):
    app, said, loads = _app(cfg, monkeypatch, {"small": 0.62}, voice="friday")    # as it was chosen
    asyncio.run(app.upgrade_voice())
    assert [t for t, _ in loads] == ["small"] and app.speaker.backend.voice == "friday"
    assert q.group_for("friday") == "human_small" and q.group_for("friday_calm") == "human"
    assert (q.VOICES_DIR / "friday.wav").exists() and (q.VOICES_DIR / "friday_calm.wav").exists()
    app.speaker.backend.close()


def test_both_too_slow_means_classic_said_once(cfg, monkeypatch):
    app, said, loads = _app(cfg, monkeypatch, {"large": 1.3, "small": 1.1})
    asyncio.run(app.upgrade_voice())
    assert app.speaker.backend.name == "kokoro-onnx" and len(said) == 1 and "Settings" in said[0]
    assert "too slow" in app.capabilities["natural_voice"]
    asyncio.run(app.upgrade_voice())
    assert len(said) == 1 and len(loads) == 2           # remembered: not timed or said again


def test_you_can_have_it_anyway(cfg, monkeypatch):
    app, said, loads = _app(cfg, monkeypatch, {"large": 0.95, "small": 0.95}, force=True)
    asyncio.run(app.upgrade_voice())
    assert app.speaker.backend.name == "qwen3" and app.speaker.backend.tier == "large"
    assert "forced on" in app.capabilities["natural_voice"] and not said
    app.speaker.backend.close()


def test_live_speech_goes_before_pre_rendering(tmp_path):
    """Pre-rendered acknowledgements wait behind anything he's about to say live."""
    import threading
    import time

    nv = q.QwenVoice("butler", tmp_path)
    order, gate, busy = [], threading.Event(), threading.Event()

    def hold():
        busy.set()
        gate.wait(5)
    threads = [threading.Thread(target=lambda: nv._call(hold))]
    threads[0].start()
    assert busy.wait(2)                                      # the model is busy with something
    for i in range(3):                                       # meanwhile: three pre-renders queue up…
        threads.append(threading.Thread(target=lambda i=i: nv._call(lambda: order.append(f"later{i}"), later=True)))
        threads[-1].start()
    time.sleep(0.1)
    threads.append(threading.Thread(target=lambda: nv._call(lambda: order.append("live"))))   # …then a reply
    threads[-1].start()
    time.sleep(0.1)
    gate.set()
    for t in threads:
        t.join(5)
    nv.close()
    assert order[0] == "live" and sorted(order[1:]) == ["later0", "later1", "later2"], order
