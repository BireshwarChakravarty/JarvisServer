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


def test_upgrade_switches_only_when_ready_and_fast(cfg, monkeypatch, tmp_path):
    from jarvis.core.orchestrator import Jarvis

    app = Jarvis(cfg, headless=True)
    app.cfg.data.setdefault("voice", {}).setdefault("tts", {})["voice"] = "butler"
    app.speaker = SimpleNamespace(backend=SimpleNamespace(name="kokoro-onnx"), _cache={}, prepare=lambda t: None)
    monkeypatch.setattr(q, "supported", lambda: True)
    monkeypatch.setattr(q, "model_ready", lambda d: True)
    said = []

    async def announce(text, priority="normal"):
        said.append(text)
    app.announce = announce

    class Slow(q.QwenVoice):
        def load(self):
            raise q.TooSlow("1.40x real time")
    monkeypatch.setattr(q, "QwenVoice", Slow)
    asyncio.run(app.upgrade_voice())
    assert app.speaker.backend.name == "kokoro-onnx" and said and "too slow" in said[0]

    class Fast(q.QwenVoice):
        def load(self):
            return 0.4
    monkeypatch.setattr(q, "QwenVoice", Fast)
    asyncio.run(app.upgrade_voice())
    assert app.speaker.backend.name == "qwen3" and app.capabilities["tts"] == "qwen3"
    app.speaker.backend.close()
