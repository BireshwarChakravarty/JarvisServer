import asyncio
import datetime as dt

from jarvis.core.persona import build_system_prompt, context_line, fallback_greeting, part_of_day
from jarvis.core.protocols import Protocols
from jarvis.tools.registry import ToolResult


def test_part_of_day_boundaries():
    at = lambda h: dt.datetime(2026, 11, 14, h, 0)
    assert part_of_day(at(5)) == "morning"
    assert part_of_day(at(12)) == "afternoon"
    assert part_of_day(at(17)) == "evening"
    assert part_of_day(at(23)) == "late_night" and part_of_day(at(2)) == "late_night"


def test_greetings_and_prompt():
    g = fallback_greeting("Tony", "sir", now=dt.datetime(2026, 11, 14, 8, 15), extras=["It's 9 degrees."])
    assert g.startswith("Good morning, sir.") and "8:15 AM" in g and "9 degrees" in g
    back = fallback_greeting("Tony", "boss", kind="return", now=dt.datetime(2026, 11, 14, 15, 0))
    assert "boss" in back
    p = build_system_prompt("Tony", "sir")
    assert "Tony" in p and '"sir"' in p and "{" not in p
    line = context_line(dt.datetime(2026, 11, 14, 9, 5), "GMT", {"battery": "80%", "empty": ""})
    assert line.startswith("[context]") and "battery: 80%" in line and "empty" not in line


class FakeRegistry:
    def __init__(self):
        self.calls = []

    async def call(self, name, args, ctx):
        self.calls.append((name, args))
        return ToolResult("ok")


class FakeOrch:
    def __init__(self):
        self.said, self.queued = [], []

    async def say(self, text):
        self.said.append(text)

    def queue_request(self, text):
        self.queued.append(text)


def test_protocols_match_and_run(tmp_path):
    from jarvis.tools.context import ToolContext

    protos = Protocols(tmp_path / "p.yaml")  # writes defaults
    assert protos.find("engage house party protocol")[0] == "house_party_protocol"
    assert protos.find("party mode")[0] == "house_party_protocol"
    assert protos.find("Work Mode")[0] == "work_mode"
    assert protos.find("good night")[0] == "good_night"
    assert protos.find("self destruct") is None

    reg, orch = FakeRegistry(), FakeOrch()
    ctx = ToolContext(cfg=None, orchestrator=orch)
    out = asyncio.run(protos.run("work mode", reg, ctx))
    assert "complete" in out
    assert ("set_volume", {"level": 25}) in reg.calls
    assert ("open_application", {"name": "Calendar"}) in reg.calls
    assert orch.said and orch.queued  # spoke the intro and queued the 'ask' step
