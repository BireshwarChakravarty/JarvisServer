"""Instant answers: everyday commands done at once, without an AI round trip."""
import asyncio
import datetime as dt
import random

import pytest

from jarvis.core import quick

NOW = dt.datetime(2026, 11, 17, 16, 20)


def m(text):
    return quick.match(text, now=NOW, rng=random.Random(1))


@pytest.mark.parametrize("text,secs,label", [
    ("Set a timer for 10 minutes", 600, ""), ("hey jarvis, set a pasta timer for nine minutes please", 540, "pasta"),
    ("ten minute timer", 600, ""), ("start a timer for an hour and a half", 5400, ""),
    ("set a timer for half an hour", 1800, ""), ("timer for 90 seconds", 90, ""),
    ("set a timer for 1 hour 20 minutes", 4800, ""), ("Could you set a tea timer for five minutes?", 300, "tea"),
])
def test_timers(text, secs, label):
    q = m(text)
    assert q and q.tool == "set_timer" and q.args == {"seconds": secs, "label": label}, (text, q)
    assert q.reply and q.note


@pytest.mark.parametrize("text", [
    "set a timer for when the oven is hot", "set a timer for ten minutes after the meeting ends",
    "what's the time in Tokyo", "play something relaxing", "next", "play", "skip", "previous",
    "turn it up a lot and play jazz", "lock the front door", "set the volume to 400",
    "how long is left on my timer", "",
])
def test_anything_more_goes_to_the_brain(text):
    assert m(text) is None, text


def test_time_and_date_are_answered_from_the_clock():
    assert m("What time is it?").reply in ("It's 4:20 PM.", "4:20 PM.")
    assert m("Jarvis, what's the date today").reply == "Tuesday, the 17th of November."
    assert quick.match("what day is it", now=dt.datetime(2026, 11, 22)).reply.startswith("Sunday, the 22nd")
    assert quick.match("what's the date", now=dt.datetime(2026, 11, 11)).reply.endswith("11th of November.")


@pytest.mark.parametrize("text,tool,args", [
    ("pause", "media_control", {"action": "pause"}), ("pause the music", "media_control", {"action": "pause"}),
    ("next song", "media_control", {"action": "next"}), ("skip this track", "media_control", {"action": "next"}),
    ("previous track", "media_control", {"action": "previous"}), ("resume the music", "media_control", {"action": "play"}),
    ("volume to 30", "set_volume", {"level": 30}), ("set the volume to 75 percent", "set_volume", {"level": 75}),
    ("turn it up", "volume_step", {"delta": 15}), ("a bit quieter please", "volume_step", {"delta": -15}),
    ("mute", "mute", {"muted": True}), ("unmute the sound", "mute", {"muted": False}),
    ("lock the mac", "system_action", {"action": "lock_screen"}), ("cancel the timers", "cancel_timer", {}),
    ("air mouse", "air_mouse", {"on": True}), ("turn on the air mouse", "air_mouse", {"on": True}),
    ("air mouse off", "air_mouse", {"on": False}), ("stop the air mouse", "air_mouse", {"on": False}),
])
def test_everyday_commands(text, tool, args):
    q = m(text)
    assert q and q.tool == tool and q.args == args, (text, q)


def test_durations_are_said_naturally():
    assert quick.say_duration(5400) == "1 hour and 30 minutes"
    assert quick.say_duration(65) == "1 minute and 5 seconds"
    assert quick.say_duration(3725) == "1 hour, 2 minutes and 5 seconds"


def test_orchestrator_answers_instantly_and_tells_the_brain_next_turn():
    from types import SimpleNamespace

    import jarvis.tools.builtin  # noqa: F401
    from jarvis.core.orchestrator import Jarvis
    from jarvis.tools.registry import registry

    said, added = [], []
    sched = SimpleNamespace(add_timer=lambda s, l: added.append((s, l)) or {"message": l or "timer"}, list=list)
    j = Jarvis.__new__(Jarvis)
    j.cfg = SimpleNamespace(get=lambda k, d=None: d)
    j.speaker = SimpleNamespace(enqueue=said.append)
    j.ctx = SimpleNamespace(scheduler=sched, confirm=None, require_confirmation=False)

    async def brain_should_not_run(*a, **k):
        raise AssertionError("the brain was asked")
    j.brain = SimpleNamespace(respond=brain_should_not_run)
    reply = asyncio.run(Jarvis.handle_text(j, "set a pasta timer for nine minutes", source="voice"))
    assert added == [(540, "pasta")] and said == [reply] and "9 minutes" in reply.lower()
    assert j._quick_notes == ["set a 9 minutes pasta timer"]
    assert asyncio.run(Jarvis._quick_answer(j, "tell me a story")) is None
    del registry
