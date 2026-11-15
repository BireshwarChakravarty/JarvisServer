"""Built-in protocols are always there, merge with yours, can be hidden; the new step types work."""
import asyncio
from types import SimpleNamespace

import yaml

from jarvis.core.protocols import DEFAULT_PROTOCOLS, Protocols

STEP_TYPES = {"open_app", "quit_app", "open_url", "shortcut", "volume", "brightness", "dark_mode", "music", "media",
              "notify", "applescript", "shell", "lock_screen", "sleep_display", "wait", "say", "ask", "timer",
              "hand_control", "quit_apps", "hide_others"}


def test_every_builtin_is_well_formed():
    data = yaml.safe_load(DEFAULT_PROTOCOLS)
    assert len(data) >= 13
    for name, p in data.items():
        assert p.get("description") and p.get("aliases"), name
        for step in p.get("steps", []):
            assert len(step) == 1 and next(iter(step)) in STEP_TYPES, (name, step)


def test_builtins_merge_with_your_file_and_can_be_hidden(tmp_path):
    f = tmp_path / "protocols.yaml"
    f.write_text("work_mode:\n  description: mine\n  steps: []\nmovie_night:\n  disabled: true\n"
                 "studio:\n  aliases: [studio time]\n  steps: [{volume: 40}]\n")
    p = Protocols(f)
    names = {x["name"] for x in p.list()}
    assert "focus_sprint" in names and "studio" in names and "movie_night" not in names
    assert p.data["work_mode"]["description"] == "mine"                 # yours wins
    assert p.find("pomodoro")[0] == "focus_sprint" and p.find("be right back")[0] == "lockdown"
    assert any(x["pinned"] for x in p.list())


def test_new_step_types(tmp_path):
    p = Protocols(tmp_path / "protocols.yaml")
    timers, hands, calls = [], [], []

    class Reg:
        async def call(self, tool, args, ctx):
            calls.append((tool, args))
            return SimpleNamespace(summary="ok")

    ctx = SimpleNamespace(scheduler=SimpleNamespace(add_timer=lambda s, label: timers.append((s, label))),
                          orchestrator=SimpleNamespace(set_hand_control=lambda on: hands.append(on) or "on"))

    async def run():
        f = []
        await p._step("timer", {"minutes": 25, "label": "Focus sprint"}, Reg(), ctx, f)
        await p._step("timer", 5, Reg(), ctx, f)
        await p._step("hand_control", True, Reg(), ctx, f)
        await p._step("quit_apps", ["Mail", "Slack"], Reg(), ctx, f)
        await p._step("hide_others", True, Reg(), ctx, f)
        await p._step("sleep_display", True, Reg(), ctx, f)
    asyncio.run(run())
    assert timers == [(1500.0, "Focus sprint"), (300.0, "")] and hands == [True]
    assert [c[0] for c in calls] == ["quit_application", "quit_application", "hide_other_apps", "system_action"]
