"""Greetings stay fresh: no dull filler, a rotating angle, changes since last time, recent lines avoided."""
import datetime as dt
import random

from jarvis.core import greeting_style as gs


def test_trim_drops_power_status_unless_battery_is_low():
    assert "battery" not in gs.trim({"battery": {"state": "AC power"}})
    assert "battery" not in gs.trim({"battery": {"percent": 80, "state": "discharging"}})
    assert "battery" in gs.trim({"battery": {"percent": 12, "state": "discharging"}})


def test_changes_since_last_briefing():
    before = {"weather": {"temperature": 14, "condition": "clear"}, "calendar_today": [{"title": "Stand-up"}]}
    now = {"weather": {"temperature": 21, "condition": "rain"},
           "calendar_today": [{"title": "Stand-up"}, {"title": "Board call"}]}
    d = gs.changes(now, before)
    assert any("14 to 21" in x for x in d) and any("rain" in x for x in d) and any("Board call" in x for x in d)
    assert gs.changes(now, None) == []


def test_angles_rotate_and_never_repeat_back_to_back():
    data = {"calendar_today": [{"title": "x"}], "weather": {"temperature": 20, "rain_chance_12h": 70},
            "last_conversation": "the board deck"}
    rng, recent, seen = random.Random(3), [], []
    for _ in range(30):
        a = gs.pick_angle(data, ["new on the calendar: x"], 3600, recent, dt.datetime(2026, 11, 13, 9), rng)
        assert not recent or a != recent[-1]["angle"]
        recent.append({"angle": a}); seen.append(a)
    assert len(set(seen)) >= 4


def test_instructions_carry_variety_and_avoid_recent_lines():
    recent = [{"text": "Good morning, sir. It's a fine day.", "angle": "weather"}]
    text = gs.instructions("sir", "login", {"part_of_day": "morning"}, ["weather turned rain"], "change",
                           recent, "8 hours", random.Random(1))
    assert "Angle for this one" in text and "Changed since you last spoke" in text
    assert "It's a fine day" in text and "not a generic" in text


def test_remember_keeps_a_short_history(tmp_path):
    from jarvis.core.memory import Memory

    m = Memory(tmp_path / "m.db")
    for i in range(9):
        gs.remember(m, f"line {i}", "agenda", {"weather": {"temperature": i}})
    r = m.get(gs.RECENT_KEY)
    assert len(r) == 6 and r[-1]["text"] == "line 8" and m.get(gs.SNAPSHOT_KEY)["weather"]["temperature"] == 8
