"""Onboarding API, model manager and news parsing (all platform-independent)."""
import asyncio
import http.server
import socketserver
import threading
from pathlib import Path

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
    from jarvis.events import bus
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
        q = asyncio.Queue()
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
