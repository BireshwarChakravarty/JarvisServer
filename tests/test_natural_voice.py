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
    assert 1_600_000_000 < row["total"] < 1_800_000_000 and not row["ready"]      # the lighter model only


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


def test_every_natural_voice_runs_on_the_lighter_model(cfg, monkeypatch):
    for voice in q.HUMAN_VOICES:
        assert q.tiers_for(voice) == ["small"] and q.group_for(voice) == "human"
        assert (q.VOICES_DIR / f"{voice}.wav").exists()
    app, said, loads = _app(cfg, monkeypatch, {"small": 0.62}, voice="friday")
    asyncio.run(app.upgrade_voice())
    assert app.speaker.backend.name == "qwen3" and app.speaker.backend.voice == "friday" and not said
    assert app.capabilities["natural_voice"].startswith("Friday (")
    app.speaker.backend.close()
    app.speaker.backend = SimpleNamespace(name="kokoro-onnx")
    asyncio.run(app.upgrade_voice())                   # next start: no re-timing
    assert loads == [("small", True), ("small", False)]
    app.speaker.backend.close()


def test_too_slow_means_classic_said_once(cfg, monkeypatch):
    app, said, loads = _app(cfg, monkeypatch, {"small": 1.1})
    asyncio.run(app.upgrade_voice())
    assert app.speaker.backend.name == "kokoro-onnx" and len(said) == 1 and "Settings" in said[0]
    assert "too slow" in app.capabilities["natural_voice"]
    asyncio.run(app.upgrade_voice())
    assert len(said) == 1 and len(loads) == 1           # remembered: not timed or said again


def test_you_can_have_it_anyway(cfg, monkeypatch):
    app, said, loads = _app(cfg, monkeypatch, {"small": 0.95}, force=True)
    asyncio.run(app.upgrade_voice())
    assert app.speaker.backend.name == "qwen3" and "forced on" in app.capabilities["natural_voice"] and not said
    app.speaker.backend.close()


def test_models_no_longer_used_are_deleted(tmp_path):
    from jarvis.models import ModelManager

    mm = ModelManager(tmp_path)
    for d in ("qwen3-tts-1.7b-base-4bit", "qwen3-tts-0.6b-base-4bit", "whisper-large-v3-turbo", "whisper-small.en",
              "kokoro-keep"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "model.bin").write_bytes(b"x" * 10)
    assert sorted(mm.prune(keep_natural=True)) == ["qwen3-tts-1.7b-base-4bit", "whisper-large-v3-turbo"]
    assert (tmp_path / "qwen3-tts-0.6b-base-4bit").exists() and (tmp_path / "whisper-small.en").exists()
    assert mm.prune(keep_natural=False) == ["qwen3-tts-0.6b-base-4bit"]       # a Classic voice is chosen
    assert (tmp_path / "whisper-small.en").exists() and (tmp_path / "kokoro-keep").exists()


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
