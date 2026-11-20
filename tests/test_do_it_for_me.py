"""'Do it for me': JARVIS clicks, types and presses keys himself, showing the ring first, asking
before anything consequential. The Mac event posting itself is exercised by the in-app self-test."""
import asyncio
from types import SimpleNamespace

import pytest

from jarvis.core.guide import Guide
from jarvis.mac import control


def test_keys_parse_and_risky_combos():
    assert control.parse_keys("cmd+shift+t") == ({"command", "shift"}, "t")
    assert control.parse_keys("Return") == (set(), "return")
    assert control.parse_keys("⌘ + ,") == ({"command"}, ",")
    for bad in ("", "cmd", "cmd+banana"):
        with pytest.raises(ValueError):
            control.parse_keys(bad)
    assert control.risky_keys("cmd+delete") and control.risky_keys("cmd+q") and not control.risky_keys("cmd+t")


def test_risky_labels():
    for label in ("Send", "Delete Message", "Buy Now", "Pay £20", "Sign Out", "Empty Bin", "Reply All", "Install"):
        assert control.is_risky(label), label
    for label in ("Open", "New Tab", "Bold", "Preferences…", "Sender name", "Payment history"):
        assert not control.is_risky(label), label


class Hands:
    is_risky = staticmethod(control.is_risky)

    def __init__(self):
        self.log = []

    def bring_to_front(self, pid, app):
        self.log.append(("front", pid, app))

    def click(self, x, y, double=False):
        self.log.append(("click", round(x), round(y), double))

    def type_text(self, text):
        self.log.append(("type", text))

    def press(self, keys):
        self.log.append(("press", keys))


class Overlay:
    def __init__(self):
        self.shown = []

    def show(self, rect, caption="", **k):
        self.shown.append(caption)

    def done(self, ok):
        pass


def el(label, x=100, y=200, w=80, h=20, pid=42, app="Mail"):
    return SimpleNamespace(label=label, x=x, y=y, w=w, h=h, app=app, kind="button", area="window", extra={"pid": pid})


def guide(found):
    g = Guide(SimpleNamespace(get=lambda k, d=None: d, set=lambda k, v: None), resolve=lambda t: found.get(t),
              overlay=Overlay(), hands=Hands())
    return g


def test_click_brings_the_app_forward_shows_the_ring_then_clicks_the_middle():
    g = guide({"Archive": el("Archive")})
    out = asyncio.run(g.act("Archive", "click"))
    assert out["status"] == "done" and out["did"] == "clicked Archive"
    assert g.hands.log == [("front", 42, "Mail"), ("click", 140, 210, False)]
    assert g.overlay.shown == ["Clicking Archive"]


def test_type_into_a_field_and_submit():
    g = guide({"search": el("Search", x=10, y=10, w=200, h=30, app="Safari")})
    asked = []

    async def confirm(what):
        asked.append(what)
        return True
    out = asyncio.run(g.act("search", "type", text="weather in London", submit=True, confirm=confirm))
    assert out["status"] == "done" and asked == ["type “weather in London” into Search and press Return in Safari"]
    assert g.hands.log[1:] == [("click", 110, 25, False), ("type", "weather in London"), ("press", "return")]


def test_a_risky_click_is_asked_first_and_a_no_does_nothing():
    g = guide({"Send": el("Send")})

    async def no(what):
        return False
    out = asyncio.run(g.act("Send", "click", confirm=no))
    assert out["status"] == "declined" and g.hands.log == []

    async def yes(what):
        return True
    assert asyncio.run(g.act("Send", "click", confirm=yes))["status"] == "done"


def test_not_on_screen_says_what_to_do():
    out = asyncio.run(guide({}).act("Nowhere", "click"))
    assert out["status"] == "not_found" and "find_on_screen" in out["advice"]


def test_the_tools_are_registered_and_explain_themselves():
    from jarvis.tools import builtin  # noqa: F401
    from jarvis.tools.registry import registry

    for name in ("click_on_screen", "type_on_screen", "press_keys", "scroll_screen"):
        assert registry.get(name) is not None, name
    ctx = SimpleNamespace(orchestrator=None, confirm=None, require_confirmation=True)
    assert "isn't available" in asyncio.run(builtin.click_on_screen(ctx, "Archive"))
    assert "don't know that key" in asyncio.run(builtin.press_keys(ctx, "cmd+banana"))
