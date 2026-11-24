"""Onboarding API, model manager and news parsing (all platform-independent)."""
import http.server
import socketserver
import threading

from fastapi.testclient import TestClient


def make_client(cfg):
    from jarvis.app.supervisor import Supervisor
    from jarvis.hud.server import create_app

    sup = Supervisor(cfg)
    return sup, TestClient(create_app(supervisor=sup))


def test_routes_redirect_to_setup_until_complete(cfg):
    sup, c = make_client(cfg)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/setup"
    assert c.get("/setup").status_code == 200
    (cfg.home / ".setup-complete").write_text("x")
    assert c.get("/", follow_redirects=False).status_code == 200


def test_profile_state_and_finish_gating(cfg, monkeypatch):
    sup, c = make_client(cfg)
    assert c.post("/api/setup/profile", json={"name": ""}).status_code == 400
    assert c.post("/api/setup/profile", json={"name": "Pepper", "title": "ma'am", "city": "Malibu",
                                               "units": "imperial", "latitude": 34.0, "longitude": -118.7}).json()["ok"]
    st = c.get("/api/setup/state").json()
    assert st["profile"] == {"name": "Pepper", "title": "ma'am", "city": "Malibu", "units": "imperial"}
    assert sup.cfg.get("owner.name") == "Pepper"  # live config updated too
    assert {m["key"] for m in st["models"]} == {"wake", "voice", "ears", "eyes"}
    # no key yet
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert c.post("/api/setup/finish", json={}).status_code == 400
    assert c.post("/api/setup/key", json={"key": "not-a-key"}).status_code == 400
    # key present but models missing
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    r = c.post("/api/setup/finish", json={})
    assert r.status_code == 400 and "downloading" in r.json()["error"].lower()
    # pretend the essential models exist
    for g in sup.models.groups:
        if g.key in ("wake", "ears", "voice"):
            for a in g.assets:
                p = sup.models.path(a.name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x")
    started = []

    async def fake_start(greet=True):
        started.append(greet)

    sup.start_jarvis = fake_start
    r = c.post("/api/setup/finish", json={"sentry_mode": True})
    assert r.json() == {"ok": True, "redirect": "/"}
    assert (cfg.home / ".setup-complete").exists()
    assert sup.cfg.get("vision.sentry_mode") is True


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def test_model_manager_downloads_with_progress_and_mirrors(tmp_path):
    from jarvis.models import Asset, Group, ModelManager

    src = tmp_path / "srv"
    src.mkdir()
    (src / "a.bin").write_bytes(b"A" * 300_000)
    (src / "b.bin").write_bytes(b"B" * 1000)
    handler = lambda *a, **k: _Quiet(*a, directory=str(src), **k)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        mm = ModelManager(tmp_path / "models")
        mm.groups = [Group("g", "Test", "test", [
            Asset("sub/a.bin", [f"http://127.0.0.1:{port}/missing.bin", f"http://127.0.0.1:{port}/a.bin"], 10),
            Asset("b.bin", [f"http://127.0.0.1:{port}/b.bin"], 10),
        ])]
        assert mm.ensure("g")
        assert (tmp_path / "models/sub/a.bin").stat().st_size == 300_000
        assert mm.ready("g") and mm.status()[0]["ready"]
        assert mm.status()[0]["total"] == 301_000
        # a second call is a no-op
        assert mm.ensure("g")
    finally:
        httpd.shutdown()


def test_news_parser_rss_and_atom():
    from jarvis.integrations.news import parse_feed

    rss = """<rss><channel><title>BBC</title><item><title>One</title><link>http://a</link></item>
             <item><title>Two</title></item></channel></rss>"""
    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><title>HN</title>
              <entry><title>Three</title><link href="http://c"/></entry></feed>"""
    assert [i["title"] for i in parse_feed(rss)] == ["One", "Two"]
    assert parse_feed(rss)[0]["source"] == "BBC"
    assert parse_feed(atom) == [{"title": "Three", "link": "http://c", "source": "HN"}]


def test_permissions_and_login_item_are_safe_off_mac():
    from jarvis.mac import login_item, permissions

    sts = permissions.all_status()
    assert {p["key"] for p in sts} >= {"microphone", "camera", "calendar", "automation"}
    if not permissions.IS_MAC:
        assert all(p["status"] == "unavailable" for p in sts)
        assert login_item.status() == "unavailable"


def test_menu_pages_open_on_jarvis_own_server(cfg):
    """The menu's Settings… and Pair iPhone or iPad… must load from JARVIS's server, not pywebview's."""
    from jarvis.app.supervisor import Supervisor

    sup = Supervisor(cfg)
    loaded = []

    class Window:
        def load_url(self, u): loaded.append(u)
        def show(self): pass
        def restore(self): pass

    sup.window = Window()
    for path in ("/pair", "/setup", "setup#senses", "http://127.0.0.1:7777/"):
        sup._show_window(path)
    port = int(cfg.get("hud.port", 7777))
    assert loaded == [f"http://127.0.0.1:{port}/pair", f"http://127.0.0.1:{port}/setup",
                      f"http://127.0.0.1:{port}/setup#senses", "http://127.0.0.1:7777/"]


def test_showing_the_window_never_blocks_the_caller(cfg):
    """pywebview's window calls wait for the main thread (up to 20 s). Called on JARVIS's event loop
    that froze everything, including the iPhone companion's buttons."""
    import threading
    import time

    from jarvis.app.supervisor import Supervisor

    sup = Supervisor(cfg)
    release, shown = threading.Event(), threading.Event()

    class SlowWindow:
        def load_url(self, u): pass
        def show(self):
            release.wait(5)
            shown.set()
        def restore(self): pass

    sup.window = SlowWindow()
    t0 = time.monotonic()
    sup.show_window("/pair")
    assert time.monotonic() - t0 < 0.2
    release.set()
    assert shown.wait(5)


def test_a_broken_config_file_doesnt_stop_jarvis_starting(tmp_path, monkeypatch):
    from jarvis.config import load_config

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("owner: {name: Tony\n  title: [oops")
    cfg = load_config()
    assert cfg.get("voice.wake_word") == "hey_jarvis"                      # defaults
    assert (tmp_path / "config.yaml.broken").exists()
    (tmp_path / "config.yaml").write_text("just a sentence")
    assert load_config().get("hud.port") == 7777


def test_settings_are_saved_all_or_nothing(tmp_path, monkeypatch):
    from jarvis.app.setup_api import _write_user_config
    from jarvis.config import load_config

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    cfg = load_config()
    _write_user_config(cfg, {"owner": {"name": "Tony"}})
    assert "Tony" in (tmp_path / "config.yaml").read_text() and not (tmp_path / "config.yaml.tmp").exists()


def test_natural_voice_override_switch(cfg):
    sup, c = make_client(cfg)
    assert c.get("/api/setup/voices").json()["natural_force"] is False
    assert c.post("/api/setup/natural-force", json={"enabled": True}).json() == {"ok": True, "enabled": True}
    assert "natural_force: true" in cfg.user_config_path.read_text()
    assert c.get("/api/setup/voices").json()["natural_force"] is True
