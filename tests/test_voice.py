"""Voice: spoken-form text, voice blends, phrase merging, Apple voice choice and setup endpoints."""
import numpy as np
import pytest

from jarvis.voice.text import speech_form
from jarvis.voice.tts import (VOICE_PRESETS, SilentTTS, Speaker, best_mac_voice, breath_after,
                              parse_voice_spec, shape_audio)


@pytest.mark.parametrize("written,spoken", [
    ("Your board call is at 11:04 AM, sir.", "Your board call is at eleven oh four in the morning, sir."),
    ("A board call at 9:00 AM tomorrow, sir.", "A board call at nine tomorrow morning, sir."),
    ("Meeting at 7:30.", "Meeting at half past seven."),
    ("Dinner at 7.30pm tonight.", "Dinner at half past seven tonight."),
    ("Alarm set for 6 am.", "Alarm set for six in the morning."),
    ("Flight at 18:45.", "Flight at quarter to seven in the evening."),
    ("It is 12:00 PM.", "It is noon."),
    ("Version 2.10 shipped.", "Version 2.10 shipped."),          # a number, not a time
    ("Ratio 16:9 at 12:00:05.", "Ratio 16:9 at 12:00:05."),       # not a clock time
    ("Downloaded 3.5GB, e.g. models vs. voices.", "Downloaded 3.5 gigabytes, for example, models versus voices."),
    ("Done 🙂", "Done"),
])
def test_speech_form(written, spoken):
    assert speech_form(written) == spoken


def test_voice_specs():
    assert parse_voice_spec("bm_george") == [("bm_george", 1.0)]
    assert parse_voice_spec("bm_george:3,bm_fable:1") == [("bm_george", 0.75), ("bm_fable", 0.25)]
    assert [n for n, _ in parse_voice_spec("jarvis")] == ["bm_george", "bm_fable"]
    assert abs(sum(w for _, w in parse_voice_spec("jarvis")) - 1) < 1e-9
    for p in VOICE_PRESETS.values():
        assert all(n[:2] in ("bm", "bf", "am", "af") for n, _ in parse_voice_spec(p["spec"]))


def test_breaths_and_click_free_joins():
    assert breath_after("Shall I?") > breath_after("Done.") > breath_after("First,") > breath_after("and")
    a = shape_audio(np.ones(24000, dtype=np.float32), 24000, 0.25)
    assert a[0] == 0 and abs(a[23999]) < 0.01 and len(a) == 24000 + 6000 and not a[24000:].any()


def test_phrase_merging_keeps_counts():
    sp = Speaker(SilentTTS())
    for s in ["Second sentence here.", "Third one.", "Fourth, and a bit longer than the others."]:
        sp.enqueue(s)
    gen, first = sp._text_q.get_nowait()
    text, n = sp._take_phrase(gen, first)
    assert n == 3 and text.startswith("Second sentence here. Third one.")
    sp.enqueue("x" * 400)                      # too long to merge: left for the next phrase
    sp.enqueue("Short.")
    text, n = sp._take_phrase(sp._gen, "Lead.")
    assert n == 1 and text == "Lead." and sp._text_q.qsize() == 2
    assert sp._text_q.get_nowait()[1] == "x" * 400   # order preserved


def test_best_mac_voice():
    inst = [{"name": "Daniel", "lang": "en_GB"}, {"name": "Jamie (Premium)", "lang": "en_GB"}]
    assert best_mac_voice(inst) == "Jamie (Premium)"
    assert best_mac_voice([{"name": "Samantha", "lang": "en_US"}]) == "Daniel"


def test_voice_setup_endpoints(cfg):
    from tests.test_app_setup import make_client

    _, client = make_client(cfg)
    r = client.get("/api/setup/voices").json()
    assert r["current"] == "jarvis" and any(p["id"] == "jarvis" for p in r["presets"])
    assert client.post("/api/setup/voice", json={"voice": "lewis"}).json()["current"] == "lewis"
    assert client.post("/api/setup/voice", json={"voice": "mac:Jamie (Premium)"}).json()["current"] == "mac:Jamie (Premium)"
    assert client.post("/api/setup/voice", json={"voice": "nope"}).status_code == 400


def test_filler_covers_slow_starts_only():
    import asyncio
    from types import SimpleNamespace

    from jarvis.core.orchestrator import Jarvis as Orchestrator

    said = []
    fake = SimpleNamespace(cfg=SimpleNamespace(get=lambda k, d=None: "sir"), FILLERS=Orchestrator.FILLERS,
                           STILL=Orchestrator.STILL, speaker=SimpleNamespace(speaking=False, enqueue=said.append))

    async def run(first_after):
        first, cancel = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(Orchestrator._filler(fake, first, cancel))
        await asyncio.sleep(first_after)
        first.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(run(0.2))      # a quick answer: no filler
    assert said == []
    asyncio.run(run(1.6))      # a slow one: exactly one acknowledgement
    assert len(said) == 1 and said[0] in [f.format(t="sir") for f in Orchestrator.FILLERS]


def test_pro_plan_gets_sonnet_by_default():
    from jarvis.app.setup_api import recommended_model

    assert recommended_model("pro") == "sonnet" and recommended_model("Claude Pro") == "sonnet"
    assert recommended_model("max") == "" and recommended_model("") == ""


def test_core_loops_restart_after_a_crash():
    import asyncio
    from types import SimpleNamespace

    from jarvis.core.orchestrator import Jarvis

    runs = []

    async def flaky():
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("boom")      # first run crashes …
        return                              # … the restart completes normally

    asyncio.run(Jarvis._supervise(SimpleNamespace(), "test", flaky))
    assert len(runs) == 2


def test_switching_voices_reuses_the_loaded_model(cfg, monkeypatch):
    from types import SimpleNamespace

    from jarvis.app.supervisor import Supervisor

    sup = Supervisor(cfg)
    monkeypatch.setattr(sup.models, "ready", lambda k: True)
    loads = []

    class FakeKokoro:
        name = "kokoro-onnx"
        sample_rate = 24000

        def __init__(self):
            loads.append(1); self.voice = None; self.speed = 1.0

        def set_voice(self, v): self.voice = v

        def synth(self, t):
            import numpy as np
            return np.zeros(10, dtype="float32")

    monkeypatch.setattr("jarvis.voice.tts.make_backend", lambda *a, **k: FakeKokoro())
    for v in ("jarvis", "george", "lewis", "emma"):
        cfg.data["voice"]["tts"]["voice"] = v
        sp = sup.setup_speaker()
    assert len(loads) == 1 and sp.backend.voice == "emma"            # one model, re-pointed
    live = SimpleNamespace(speaker=SimpleNamespace(backend=FakeKokoro()))
    sup.jarvis = live
    cfg.data["voice"]["tts"]["voice"] = "fable"
    assert sup.setup_speaker() is live.speaker and live.speaker.backend.voice == "fable"   # applies to JARVIS now
