"""Mini JARVIS: the page is served and the panel is safe off-Mac / when disabled."""


def test_mini_page_served(cfg):
    from tests.test_app_setup import make_client

    _, client = make_client(cfg)
    r = client.get("/mini")
    assert r.status_code == 200 and "mini.js" in r.text and "core-gl" in r.text
    assert client.get("/static/mini.js").status_code == 200


def test_panel_disabled_and_click_through_are_safe(cfg):
    from jarvis.app import mini

    cfg.data.setdefault("app", {})["mini_panel"] = False
    p = mini.MiniPanel(cfg, "http://127.0.0.1:1/mini")
    p.create(); p.show(); p.hide()          # disabled: nothing happens, nothing raises
    assert p.window is None and not p.visible
    mini.click_through(True)                # no window yet: a no-op


class FakeWindow:
    def __init__(self):
        self.shown = 0
        self.hidden = 0
        self.native = type("N", (), {"orderFrontRegardless": lambda self: None})()

    def show(self):
        self.shown += 1

    def hide(self):
        self.hidden += 1


def test_it_shows_only_while_he_is_active_then_fades(cfg, monkeypatch):
    import time

    from jarvis.app import mini

    monkeypatch.setattr(mini, "LINGER", 0.15)
    p = mini.MiniPanel(cfg, "http://127.0.0.1:1/mini")
    p.window = FakeWindow()
    p.set_active(True)
    assert not p.visible                       # main window still open: never alongside it
    p.show()                                   # main window minimised
    assert p.visible and p.window.shown == 1
    p.set_active(False)
    assert p.visible                           # lingers a moment so the last line can be read
    time.sleep(0.3)
    assert not p.visible
    p.set_active(True)                         # "Hey Jarvis" again
    assert p.visible
    p.set_active(False)
    p.set_active(True)                         # active again before the linger ran out: stays
    time.sleep(0.3)
    assert p.visible
    p.hide()                                   # main window back
    assert not p.visible


def test_idle_hiding_can_be_switched_off(cfg):
    from jarvis.app import mini

    cfg.data.setdefault("app", {})["mini_hide_when_idle"] = False
    p = mini.MiniPanel(cfg, "http://127.0.0.1:1/mini")
    p.window = FakeWindow()
    p.show()
    assert p.visible                           # the old behaviour: always there while minimised


def test_supervisor_drives_it_from_jarvis_state(cfg):
    import asyncio

    from jarvis.app.supervisor import Supervisor
    from jarvis.events import bus

    seen = []

    class Mini:
        def set_active(self, on):
            seen.append(on)

    async def go():
        bus.bind(asyncio.get_running_loop())
        sup = Supervisor(cfg)
        sup.mini = Mini()
        task = asyncio.ensure_future(sup._mini_follows_jarvis())
        await asyncio.sleep(0.05)
        for s in ("listening", "thinking", "speaking", "idle"):
            bus.emit("state", state=s)
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(go())
    assert seen == [True, True, True, False]
