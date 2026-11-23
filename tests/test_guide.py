"""On-screen guidance: finding the right element, pointing at it, waiting for the click, and fading
help (full → hint → you've got it) as the user learns."""
import asyncio

import pytest

from jarvis.app.guide_overlay import local, screen_for, to_cocoa
from jarvis.core.guide import Guide, style_for, task_key
from jarvis.mac.screen_elements import Element, rank, score, vision_box_to_global


def el(label, role="AXButton", x=100, y=100, w=80, h=24, **kw):
    return Element(kw.pop("id", f"ax:{label}"), role, label, x, y, w, h, **kw)


# ── finding ─────────────────────────────────────────────────────────────
def test_exact_button_beats_text_that_merely_mentions_it():
    els = [el("AutoSum help: adds up a column", role="AXStaticText", y=300),
           el("AutoSum"), el("Sum", role="AXMenuItem", y=200), el("Format", role="AXMenuBarItem")]
    top = rank(els, "the AutoSum button")
    assert top[0][0].label == "AutoSum"
    assert all(e.label != "Format" for e, _ in top)


def test_menu_words_prefer_menus_and_partial_words_match():
    els = [el("Export as PDF…", role="AXMenuItem"), el("Export", role="AXButton", y=400),
           el("Exporting tips", role="AXStaticText", y=500)]
    assert rank(els, "export as pdf")[0][0].label == "Export as PDF…"
    both = [el("Export", role="AXButton", y=400), el("Export", role="AXMenuItem", y=200)]
    assert rank(both, "the export menu")[0][0].role == "AXMenuItem"
    assert rank(both, "the export button")[0][0].role == "AXButton"
    assert score(el("Preferences…", role="AXMenuItem"), "prefs") > 0          # prefix of a word


def test_disabled_tiny_and_unrelated_elements_sink():
    assert score(el("Save", w=0, h=0), "save") == 0
    assert score(el("Open"), "save") == 0
    assert score(el("Save", enabled=False), "save") < score(el("Save"), "save")
    assert score(el("Save", source="text"), "save") < score(el("Save"), "save")


def test_duplicates_in_the_same_place_are_merged():
    els = [el("Share", id="ax:1"), el("Share", id="ax:2", x=101), el("Share", id="ax:3", x=600)]
    assert len(rank(els, "share")) == 2


# ── coordinates ─────────────────────────────────────────────────────────
def test_vision_box_maps_to_global_top_left_points():
    main = (0.0, 0.0, 1440.0, 900.0)
    x, y, w, h = vision_box_to_global((0.25, 0.75, 0.1, 0.05), main)     # Vision: origin bottom-left
    assert (round(x), round(y), round(w), round(h)) == (360, 180, 144, 45)
    side = (1440.0, -200.0, 1920.0, 1080.0)                             # a second display, raised
    x, y, *_ = vision_box_to_global((0.0, 0.0, 0.5, 0.5), side)
    assert (x, y) == (1440.0, -200.0 + 540.0)


def test_overlay_goes_on_the_display_that_holds_the_target():
    screens = [(0, 0, 1440, 900), (1440, -200, 1920, 1080)]
    assert screen_for((100, 100, 50, 20), screens) == screens[0]
    assert screen_for((2000, 0, 50, 20), screens) == screens[1]
    assert screen_for((99999, 0, 5, 5), screens) == screens[0]           # off every display: main
    assert to_cocoa((1440, -200, 1920, 1080), 900) == (1440, 20, 1920, 1080)
    assert local((2000, 0, 50, 20), screens[1]) == (560, 200, 50, 20)


# ── pointing and learning ───────────────────────────────────────────────
class Memory(dict):
    def set(self, k, v):
        self[k] = v


class Overlay:
    def __init__(self):
        self.calls = []

    def show(self, rect, **kw):
        self.calls.append(("show", rect, kw))

    def done(self, ok):
        self.calls.append(("done", ok))


class Speaker:
    def __init__(self):
        self.said = []

    def enqueue(self, t):
        self.said.append(t)


def mouse_script(events):
    """events: list of (x, y, down) returned one per poll, then the last one forever."""
    seq = list(events)

    def m():
        return seq.pop(0) if len(seq) > 1 else seq[0]
    return m


def make_guide(clicks=None, element=None):
    target = element or el("AutoSum", x=500, y=40, w=60, h=22, app="Numbers")
    g = Guide(Memory(), speaker=Speaker(), resolve=lambda t: target if "nothing" not in t else None,
              overlay=Overlay(), mouse=mouse_script(clicks or [(0, 0, True), (0, 0, False), (530, 50, True)]),
              frontmost=lambda: "Numbers")
    return g, target


def test_full_walkthrough_first_time_then_hints_then_solo():
    g, _ = make_guide()
    r = asyncio.run(g.point("AutoSum", say="Click AutoSum to total it.", task="total a column", step=1, total=1))
    assert r["status"] == "clicked" and r["style"] == "full" and r["times_completed"] == 1
    assert g.speaker.said == ["Click AutoSum to total it."]
    kind, rect, kw = g.overlay.calls[0]
    assert rect == (500, 40, 60, 22) and kw["caption"] == "Click AutoSum to total it." and kw["style"] == "full"
    assert g.overlay.calls[-1] == ("done", True)

    for n in (2, 3):                     # second and third time: hints, nothing spoken
        g.mouse = mouse_script([(0, 0, False), (520, 45, True)])
        r = asyncio.run(g.point("AutoSum", say="Click AutoSum to total it.", task="Total a column!"))
        assert r["style"] == "hint" and r["times_completed"] == n
        assert g.overlay.calls[-2][2]["caption"] == "AutoSum"
    assert g.speaker.said == ["Click AutoSum to total it."]
    assert "try it alone" in r.get("note", "")
    assert g.known("total a column")["style"] == "solo"
    assert g.forget("total a column").startswith("Forgotten") and g.known("total a column")["known"] is False


def test_a_click_elsewhere_or_a_held_button_doesnt_count():
    g, _ = make_guide([(530, 50, True), (530, 50, True), (900, 900, False), (900, 900, True), (900, 900, False),
                       (531, 51, True)])
    r = asyncio.run(g.point("AutoSum", say="x"))
    assert r["status"] == "clicked"
    polls_left = g.mouse()                       # it waited for the real press inside the ring
    assert polls_left == (531, 51, True)


def test_multi_step_counts_completion_only_on_the_last_step():
    g, _ = make_guide([(0, 0, False), (530, 50, True)])
    r1 = asyncio.run(g.point("Format", say="Open Format.", task="bold text", step=1, total=2))
    assert r1["times_completed"] == 0
    g.mouse = mouse_script([(0, 0, False), (530, 50, True)])
    r2 = asyncio.run(g.point("Bold", say="Then Bold.", task="bold text", step=2, total=2))
    assert r2["times_completed"] == 1 and g.known("bold text")["steps"] == ["AutoSum", "AutoSum"]


def test_not_on_screen_says_so_and_draws_nothing():
    g, _ = make_guide()
    r = asyncio.run(g.point("nothing like this", say="x"))
    assert r["status"] == "not_found" and "advice" in r and g.overlay.calls == []


def test_timeout_and_cancel_clean_up_the_ring():
    g2, _ = make_guide([(0, 0, False)])

    async def go():
        task = asyncio.create_task(g2.point("AutoSum", say="x"))
        await asyncio.sleep(0.15)
        g2.cancel()
        return await asyncio.wait_for(task, 2)
    r2 = asyncio.run(go())
    assert r2["status"] == "cancelled" and g2.overlay.calls[-1] == ("done", False)

    g3, _ = make_guide([(0, 0, False)])
    r3 = asyncio.run(g3._wait_click((0, 0, 10, 10), timeout=0.2))
    assert r3 == "timeout"


def test_without_the_mac_pieces_it_explains_instead_of_failing():
    g = Guide(Memory())
    assert asyncio.run(g.point("x", say="y"))["status"] == "unavailable"
    assert asyncio.run(g.find("x")) == []


def test_task_names_are_matched_loosely():
    assert task_key("Numbers", "How do I total a column?") == task_key("numbers", "total a column")
    assert style_for(0) == "full" and style_for(1) == "hint" and style_for(3) == "solo"


# ── the tools the brain calls ───────────────────────────────────────────
def test_tools_route_to_the_guide():
    from types import SimpleNamespace

    import jarvis.tools.builtin  # noqa: F401
    from jarvis.tools.registry import registry

    g, _ = make_guide([(0, 0, False), (530, 50, True)])

    async def fake_find(q):
        return [{"id": "ax:1", "label": "AutoSum", "kind": "button"}]
    g.find = fake_find
    ctx = SimpleNamespace(orchestrator=SimpleNamespace(guide=g), confirm=None, require_confirmation=False)
    found = asyncio.run(registry.call("find_on_screen", {"query": "autosum"}, ctx))
    assert "AutoSum" in found.content
    shown = asyncio.run(registry.call("point_at", {"target": "ax:1", "say": "Click AutoSum.", "task": "sum",
                                                    "step": 1, "total": 1}, ctx))
    assert "clicked" in shown.content and not shown.is_error
    mem = asyncio.run(registry.call("walkthrough_memory", {"action": "check", "task": "sum"}, ctx))
    assert '"completed": 1' in mem.content
    none = asyncio.run(registry.call("find_on_screen", {"query": "x"}, SimpleNamespace(orchestrator=None)))
    assert "isn't available" in none.content


@pytest.mark.parametrize("q", ["", "   "])
def test_empty_query_finds_nothing(q):
    assert rank([el("Save")], q) == []
