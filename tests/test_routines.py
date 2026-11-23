"""Habits from your own past requests, and looking back through past conversations."""
import datetime as dt

from jarvis.core import routines
from jarvis.core.memory import Memory


def turns_at(hour, minute, days, text, weekday_only=True):
    base = dt.datetime(2026, 11, 2, hour, minute)          # a Monday
    out, d = [], 0
    while len(out) < days:
        t = base + dt.timedelta(days=d)
        d += 1
        if weekday_only and t.weekday() >= 5:
            continue
        out.append({"role": "user", "text": text, "ts": t.timestamp()})
    return out


def test_a_weekday_morning_weather_habit():
    turns = turns_at(8, 5, 5, "what's the weather like") + turns_at(14, 0, 2, "play some music")
    habits = routines.find(turns)
    assert habits and habits[0]["topic"] == "the weather" and habits[0]["days"] == 5
    assert "around 8 in the morning on weekdays" in habits[0]["text"]
    assert all(h["topic"] != "music" for h in habits)             # two days isn't a habit
    assert routines.due_now(habits, dt.datetime(2026, 11, 20, 7, 30)) == ["the weather"]
    assert routines.due_now(habits, dt.datetime(2026, 11, 20, 13, 0)) == []


def test_scattered_requests_are_not_a_habit():
    turns = []
    for i, h in enumerate((7, 11, 15, 19, 22)):
        turns += [{"role": "user", "text": "check my email", "ts": dt.datetime(2026, 11, 3 + i, h).timestamp()}]
    assert routines.find(turns) == []


def test_looking_back_by_day_and_topic(tmp_path):
    m = Memory(tmp_path / "m.db")
    m.start_session("s1")
    m.log_turn("s1", "user", "When does my flight to Delhi leave?")
    m.log_turn("s1", "assistant", "Seven forty tomorrow morning, from terminal three.")
    m.start_session("s2")
    m.log_turn("s2", "user", "Play something calm")
    got = m.conversations("flight")
    assert [t["role"] for t in got] == ["user", "assistant"] and "terminal three" in got[1]["text"]
    assert len(m.conversations()) == 3
    assert m.conversations("submarine") == []


def test_periods():
    now = dt.datetime(2026, 11, 25, 15, 0)                  # a Wednesday
    s, u, label = routines.period("yesterday", now)
    assert dt.datetime.fromtimestamp(s) == dt.datetime(2026, 11, 24) and label == "yesterday"
    s, u, label = routines.period("monday", now)
    assert dt.datetime.fromtimestamp(s) == dt.datetime(2026, 11, 23) and label == "on Monday"
    s, u, label = routines.period("last week", now)
    assert dt.datetime.fromtimestamp(s) == dt.datetime(2026, 11, 16)
    assert routines.period("", now)[2] == "in the last week"
