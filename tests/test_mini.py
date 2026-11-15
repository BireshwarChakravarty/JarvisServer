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
