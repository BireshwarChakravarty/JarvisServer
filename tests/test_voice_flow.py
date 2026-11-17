"""Drives the real voice state machine with synthetic microphone frames."""
import asyncio

import numpy as np
from fakes import FakeClient, message, text_block

from jarvis.events import bus
from jarvis.voice.audio import FRAME, VAD, record_utterance


def frames(kind, n):
    if kind == "silence":
        return [np.zeros(FRAME, np.int16) for _ in range(n)]
    t = np.arange(FRAME) / 16000
    return [(9000 * np.sin(2 * np.pi * 180 * t)).astype(np.int16) for _ in range(n)]


def energy_vad():
    v = VAD()
    v._vad = None  # deterministic energy detector for synthetic audio
    return v


def test_record_utterance_endpoints():
    async def run():
        q = asyncio.Queue()
        for f in frames("silence", 4) + frames("speech", 12) + frames("silence", 14):
            q.put_nowait(f)
        audio = await record_utterance(q, energy_vad(), start_timeout=2, end_silence=0.5)
        q2 = asyncio.Queue()
        for f in frames("silence", 30):
            q2.put_nowait(f)
        nothing = await record_utterance(q2, energy_vad(), start_timeout=0.3, end_silence=0.5)
        return audio, nothing

    audio, nothing = asyncio.run(run())
    assert audio is not None and 0.9 < len(audio) / 16000 < 2.0  # speech + pre-roll + tail
    assert nothing is None


class FakeSTT:
    def __init__(self, texts):
        self.texts = list(texts)

    def transcribe(self, audio):
        return self.texts.pop(0) if self.texts else ""


def test_converse_full_turn_then_follow_up_timeout(cfg):
    from jarvis.core.orchestrator import Jarvis

    cfg.data["voice"]["follow_up_seconds"] = 0.4

    async def run():
        app = Jarvis(cfg, headless=True)
        await app.start()
        app.vad, app.wake = energy_vad(), None
        app.stt = FakeSTT(["Hey Jarvis, how was the market today?"])
        client = FakeClient([message([text_block("Up nine points, sir.")])])
        app.brain._client = client
        events = bus.subscribe()
        q = asyncio.Queue()
        for f in frames("speech", 12) + frames("silence", 14):
            q.put_nowait(f)
        await asyncio.wait_for(app._converse(q, None), 10)
        seen = []
        while not events.empty():
            seen.append(events.get_nowait())
        return client, seen

    client, seen = asyncio.run(run())
    user_text = client.requests[0]["messages"][0]["content"][-1]["text"]
    assert user_text == "how was the market today?"  # wake phrase stripped
    assert any(e["type"] == "reply" and "nine" in e["text"] for e in seen)
    states = [e["state"] for e in seen if e["type"] == "state"]
    assert "transcribing" in states and "thinking" in states and states[-1] == "idle"


def test_dismissal_ends_conversation_without_llm_call(cfg):
    from jarvis.core.orchestrator import Jarvis

    async def run():
        app = Jarvis(cfg, headless=True)
        await app.start()
        app.vad, app.wake = energy_vad(), None
        app.stt = FakeSTT(["That's all, thanks Jarvis."])
        client = FakeClient([])
        app.brain._client = client
        q = asyncio.Queue()
        for f in frames("speech", 10) + frames("silence", 14):
            q.put_nowait(f)
        await asyncio.wait_for(app._converse(q, None), 10)
        return client

    assert asyncio.run(run()).requests == []


def test_listen_trigger_survives_its_own_chime(cfg):
    """✌️ / the mic button play a chime: the mic hears it. That used to end listening at once."""
    from jarvis.core.orchestrator import Jarvis

    cfg.data["voice"]["follow_up_seconds"] = 0.4

    async def run():
        app = Jarvis(cfg, headless=True)
        await app.start()
        app.vad, app.wake = energy_vad(), None
        app.stt = FakeSTT(["", "how was the market today?"])          # the chime transcribes as nothing
        client = FakeClient([message([text_block("Up nine points, sir.")])])
        app.brain._client = client
        q = asyncio.Queue()
        for f in frames("speech", 3) + frames("silence", 12) + frames("speech", 12) + frames("silence", 14):
            q.put_nowait(f)
        await asyncio.wait_for(app._converse(q, None), 15)
        return client

    client = asyncio.run(run())
    assert client.requests and client.requests[0]["messages"][0]["content"][-1]["text"] == "how was the market today?"
