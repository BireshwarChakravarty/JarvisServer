"""Gemini Live as JARVIS's voice, against a stand-in for Google's service (no network, no key)."""
import asyncio
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

pytest.importorskip("google.genai")

from jarvis.voice import live as live_mod  # noqa: E402


def msg(**k):
    base = dict(data=None, server_content=None, tool_call=None, session_resumption_update=None, go_away=None)
    base.update(k)
    return NS(**base)


def content(**k):
    base = dict(input_transcription=None, output_transcription=None, interrupted=False, turn_complete=False)
    base.update(k)
    return NS(**base)


class FakeSession:
    """Plays a script: each step waits until the mic has sent `after` chunks, then yields messages."""

    def __init__(self, script):
        self.script, self.audio_in, self.tool_responses, self.client_content = script, 0, [], []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send_realtime_input(self, audio=None, **k):
        self.audio_in += 1

    async def send_tool_response(self, function_responses=None, **k):
        self.tool_responses.extend(function_responses or [])

    async def send_client_content(self, turns=None, turn_complete=True, **k):
        self.client_content.append(turns)

    async def receive(self):
        for after, messages in self.script:
            while self.audio_in < after:
                await asyncio.sleep(0.01)
            for m in (messages() if callable(messages) else messages):
                yield m
        while True:                       # the connection stays open; the idle watch ends the conversation
            await asyncio.sleep(0.05)


class FakeSpeaker:
    def __init__(self):
        self.feeds, self.stops, self.tones = [], 0, []
        self._open = 0

    @property
    def speaking(self):
        return self._open > 0

    def stream_external(self, sr):
        sp = self
        sp._open += 1
        chunks = []

        class Feed:
            def put(self, c):
                chunks.append(c)

            def close(self):
                sp._open = max(0, sp._open - 1)
        self.feeds.append(chunks)
        return Feed()

    def stop(self):
        self.stops += 1
        self._open = 0

    def play_tone(self, kind="wake"):
        self.tones.append(kind)


def make(cfg, monkeypatch, script, tools=None):
    from jarvis.core.orchestrator import Jarvis
    from jarvis.tools.registry import ToolRegistry

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    cfg.data["voice"]["follow_up_after_answer"] = 0.6
    cfg.data["voice"].setdefault("live", {})["first_wait"] = 1.0
    app = Jarvis(cfg, headless=True)
    app.speaker = FakeSpeaker()
    app.vad = None
    lv = live_mod.LiveVoice(app)
    session = FakeSession(script)
    connects = []

    class Client:
        class aio:
            class live:
                @staticmethod
                def connect(model, config):
                    connects.append((model, config))
                    return session
    monkeypatch.setattr(lv, "_client", lambda: Client)
    reg = tools or ToolRegistry()
    monkeypatch.setattr("jarvis.tools.registry.registry", reg)
    monkeypatch.setattr(lv, "_tools", lambda: list(reg.active()))
    return app, lv, session, connects


def mic_queue(seconds=3.0):
    q = asyncio.Queue()
    for _ in range(int(seconds / 0.08)):
        q.put_nowait(np.zeros(1280, np.int16))
    return q


def run(lv, q, timeout=10):
    return asyncio.run(asyncio.wait_for(lv.converse(q, preroll=[np.zeros(1280, np.int16)]), timeout))


def test_he_answers_in_gemini_voice_and_the_conversation_ends_when_quiet(cfg, monkeypatch):
    from jarvis.events import bus
    seen = []
    monkeypatch.setattr(bus, "emit", lambda kind, **k: seen.append((kind, k)))
    pcm = (np.ones(2400) * 1000).astype(np.int16).tobytes()
    script = [(3, [msg(server_content=content(input_transcription=NS(text="what's the time")))]),
              (4, [msg(data=pcm), msg(data=pcm),
                   msg(server_content=content(output_transcription=NS(text="Half past three, sir."))),
                   msg(session_resumption_update=NS(new_handle="h1")),
                   msg(server_content=content(turn_complete=True))])]
    app, lv, session, connects = make(cfg, monkeypatch, script)
    t0 = time.monotonic()
    assert run(lv, mic_queue()) is True
    assert time.monotonic() - t0 < 6                                    # ended by itself after the quiet
    assert len(app.speaker.feeds) == 1 and len(app.speaker.feeds[0]) == 2  # his audio went to the speaker
    assert ("reply", {"text": "Half past three, sir."}) in seen
    assert ("transcript", {"text": "what's the time", "source": "voice"}) in seen
    assert lv.resume_handle == "h1" and connects[0][0] == live_mod.MODELS[0]
    cfgobj = connects[0][1]
    assert cfgobj.speech_config.voice_config.prebuilt_voice_config.voice_name == live_mod.DEFAULT_VOICE
    assert "British accent" in cfgobj.system_instruction


def test_tools_run_and_risky_ones_wait_for_a_yes(cfg, monkeypatch):
    from jarvis.tools.registry import ToolRegistry

    reg, done = ToolRegistry(), []

    @reg.tool("Current time", {})
    def get_time(ctx):
        return "15:30"

    @reg.tool("Send a message", {"to": {"type": "string"}}, ["to"], confirm=True, confirm_message="message {to}")
    def send_message(ctx, to):
        done.append(to)
        return "sent"

    calls = lambda *c: [msg(tool_call=NS(function_calls=[NS(id=i, name=n, args=a) for i, n, a in c]))]
    script = [(2, calls(("1", "get_time", {}), ("2", "send_message", {"to": "Pepper"})))]
    app, lv, session, _ = make(cfg, monkeypatch, script, reg)
    run(lv, mic_queue())
    by = {r.id: r.response["result"] for r in session.tool_responses}
    assert by["1"] == "15:30"
    assert by["2"].startswith("NEEDS_APPROVAL") and done == []               # not sent on the first ask
    token = by["2"].split("token=")[1].split(":")[0]
    out = asyncio.run(lv._call(live_mod.CONFIRM_TOOL, {"token": token, "approved": True}))
    assert out == "sent" and done == ["Pepper"]
    assert asyncio.run(lv._call(live_mod.CONFIRM_TOOL, {"token": token, "approved": True})).startswith("Nothing")


def test_bigger_jobs_go_to_his_brain_in_the_background(cfg, monkeypatch):
    from google.genai import types

    asked = []

    async def respond(text, **k):
        asked.append(text)
        return "Booked for eight, at the Italian place."
    script = [(2, [msg(tool_call=NS(function_calls=[NS(id="9", name=live_mod.BRAIN_TOOL,
                                                         args={"task": "book a table for two at eight"})]))])]
    app, lv, session, _ = make(cfg, monkeypatch, script)
    app.brain = NS(respond=respond)
    run(lv, mic_queue())
    assert asked == ["book a table for two at eight"]
    r = session.tool_responses[0]
    assert r.response["result"].startswith("Booked") and r.scheduling == types.FunctionResponseScheduling.WHEN_IDLE
    decl = {d.name: d for d in live_mod.declarations([])}
    assert decl[live_mod.BRAIN_TOOL].behavior == types.Behavior.NON_BLOCKING


def test_talking_over_him_stops_him(cfg, monkeypatch):
    pcm = (np.ones(24000) * 1000).astype(np.int16).tobytes()
    script = [(2, [msg(data=pcm)]), (5, [msg(server_content=content(interrupted=True))])]
    app, lv, session, _ = make(cfg, monkeypatch, script)
    run(lv, mic_queue())
    assert app.speaker.stops >= 1


def test_when_gemini_cant_be_reached_the_local_voice_takes_over(cfg, monkeypatch):
    app, lv, session, _ = make(cfg, monkeypatch, [])

    class Broken:
        class aio:
            class live:
                @staticmethod
                def connect(model, config):
                    raise RuntimeError("429 RESOURCE_EXHAUSTED: quota")
    monkeypatch.setattr(lv, "_client", lambda: Broken)
    assert run(lv, mic_queue()) is False
    assert not lv.ready() and "quota" in app.capabilities["live_voice"]


def test_no_key_means_no_gemini(cfg, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("jarvis.mac.keychain.get_api_key", lambda **k: None)
    assert live_mod.wanted(cfg) is False


def test_settings_choose_the_voice_and_accent(cfg, monkeypatch):
    from tests.test_app_setup import make_client

    monkeypatch.setenv("GEMINI_API_KEY", "x" * 39)
    _, c = make_client(cfg)
    st = c.get("/api/setup/live").json()
    assert st["installed"] and st["has_key"] and st["voice"] == live_mod.DEFAULT_VOICE and st["accent"] == "british"
    assert [v["id"] for v in st["voices"]] == ["Charon", "Orus", "Algieba", "Alnilam", "Sulafat", "Algenib"]
    assert c.post("/api/setup/live", json={"voice": "Orus", "accent": "irish"}).json()["ok"]
    assert "voice: Orus" in cfg.user_config_path.read_text()
    assert c.post("/api/setup/live", json={"voice": "Nobody"}).status_code == 400
    assert c.post("/api/setup/live-key", json={"key": "short"}).status_code == 400       # never sent to Google
