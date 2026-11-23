"""Announcements are polite: a notification while you're on a call or in Focus, and otherwise a
pause in the room's conversation before he speaks."""
import asyncio
import time
from types import SimpleNamespace

from jarvis.core.orchestrator import Jarvis


class Speaker:
    def __init__(self):
        self.said, self.tones = [], []

    async def wait_done(self, timeout=None):
        pass

    def play_tone(self, kind="wake"):
        self.tones.append(kind)

    def enqueue(self, text):
        self.said.append((time.monotonic(), text))


def make(cfg, monkeypatch, busy=None):
    app = Jarvis(cfg, headless=True)
    app.speaker, app.mic, app.presence = Speaker(), SimpleNamespace(muted=False), None
    notes = []
    from jarvis.mac import session, system

    monkeypatch.setattr(session, "screen_locked", lambda: False)
    monkeypatch.setattr(system, "notify", lambda title, text, *a, **k: notes.append(text))
    monkeypatch.setattr(app, "_busy_reason", lambda: busy)
    return app, notes


def test_on_a_call_it_becomes_a_notification(cfg, monkeypatch):
    app, notes = make(cfg, monkeypatch, busy="call")
    asyncio.run(app.announce("Design review in ten minutes."))
    assert notes == ["Design review in ten minutes."] and app.speaker.said == []


def test_urgent_things_are_still_said_on_a_call(cfg, monkeypatch):
    app, notes = make(cfg, monkeypatch, busy="call")
    asyncio.run(app.announce("Tea timer is done.", "high"))
    assert [t for _, t in app.speaker.said] == ["Tea timer is done."]


def test_he_waits_for_a_pause_in_the_room(cfg, monkeypatch):
    app, notes = make(cfg, monkeypatch)

    async def run():
        app._room_speech_at = time.monotonic()            # someone is talking right now
        t0 = time.monotonic()
        await app.announce("Your next meeting starts in ten minutes.")
        return t0
    t0 = asyncio.run(run())
    (at, text), = app.speaker.said
    assert text.startswith("Your next meeting") and at - t0 >= 1.3      # not over the conversation
