"""iPhone / iPad companion: QR pairing, per-device keys, home-network only, safe command routing."""
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.remote.devices import DeviceStore
from jarvis.remote.server import COMMANDS, create_remote_app, private_client


def test_pairing_codes_and_keys(tmp_path):
    s = DeviceStore(tmp_path / "companion.json")
    code, exp = s.current_code()
    assert exp > time.time() + 500 and s.current_code()[0] == code        # stable while valid
    assert s.pair("wrong", "iPhone") is None
    d1, k1 = s.pair(code, "iPhone")
    d2, k2 = s.pair(code, "iPad")                                         # one code pairs several devices
    assert s.check(k1) == d1 and s.check(k2) == d2 and s.check("nope") is None
    assert k1 not in (tmp_path / "companion.json").read_text()            # only hashes are stored
    assert {d["name"] for d in s.list()} == {"iPhone", "iPad"}
    s.forget(d1)
    assert s.check(k1) is None and s.check(k2) == d2
    s.forget()
    assert s.check(k2) is None and s.pair(code, "x") is None              # forgetting also kills the shown code
    s._code, s._code_expires = "old", time.time() - 1
    assert s.pair("old", "x") is None                                      # expired


def test_home_network_only():
    for ip in ("192.168.1.20", "10.0.0.7", "172.16.4.2", "127.0.0.1", "fe80::1", "169.254.3.3"):
        assert private_client(ip), ip
    for ip in ("8.8.8.8", "1.1.1.1", "2606:4700::1111"):
        assert not private_client(ip), ip


class FakeJarvis:
    def __init__(self, got):
        self.got, self.ctx, self.stt = got, None, None

    async def hud_command(self, msg):
        if msg.get("type") == "protocol" and msg.get("name") == "boom":
            raise RuntimeError("kaboom")
        self.got.append(msg)

    def queue_request(self, text, source="protocol", route=None, done=None):
        self.got.append({"type": "text", "text": text, "source": source, "route": route})

    def claim_voice(self, who, hold=4.0):
        return True


@pytest.fixture
def remote(tmp_path, cfg):
    store = DeviceStore(tmp_path / "companion.json")
    got = []
    sup = SimpleNamespace(cfg=cfg, jarvis=FakeJarvis(got),
                          show_window=lambda url=None: got.append({"type": "shown"}))
    app = create_remote_app(sup, store)
    return TestClient(app), store, got


def _paired(client, store):
    code, _ = store.current_code()
    return client.post("/remote/api/pair", json={"code": code, "name": "iPhone"}).json()["token"]


def _until(ws, kind, n=40):
    for _ in range(n):
        m = ws.receive_json()
        if m.get("type") == kind:
            return m
    raise AssertionError(f"no {kind}")


def test_page_pair_and_live_link(remote):
    client, store, got = remote
    assert client.get("/remote").status_code == 200 and "remote.js" in client.get("/remote").text
    assert client.get("/remote/manifest.webmanifest").json()["display"] == "standalone"
    with pytest.raises(WebSocketDisconnect):                                # no key: refused
        with client.websocket_connect("/remote/ws") as ws:
            ws.receive_json()
    assert client.post("/remote/api/pair", json={"code": "guess", "name": "x"}).status_code == 403
    token = _paired(client, store)
    with client.websocket_connect(f"/remote/ws?token={token}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["device"] and hello["voice"]["secure"] is False
        ws.send_text(json.dumps({"type": "text", "text": "Lights on", "id": 1}))
        assert _until(ws, "ack") == {"type": "ack", "id": 1, "for": "text", "ok": True, "note": "Sent"}
        ws.send_text(json.dumps({"type": "confirm", "approved": True, "id": 2}))
        assert _until(ws, "ack")["id"] == 2
        ws.send_text(json.dumps({"type": "shell", "command": "rm -rf /", "id": 3}))  # not on the list: ignored
        ws.send_text(json.dumps({"type": "show", "id": 4}))
        a = _until(ws, "ack")
        assert a["id"] == 4 and a["ok"]                                      # no ack at all for 'shell'
    kinds = [m["type"] for m in got]
    assert kinds == ["text", "confirm", "shown"] and got[0]["text"] == "Lights on"
    assert got[0]["source"] == "phone" and got[0]["route"] is None           # typed, reply on the Mac
    assert "shell" not in COMMANDS


def test_every_button_is_acknowledged_and_a_failure_keeps_the_link(remote):
    client, store, got = remote
    token = _paired(client, store)
    with client.websocket_connect(f"/remote/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "ping", "t": 5}))
        assert _until(ws, "pong")["t"] == 5                                  # heartbeat
        for i, msg in enumerate([{"type": "stop"}, {"type": "mute", "muted": True},
                                 {"type": "hand_control", "on": True}, {"type": "protocol", "name": "work_mode"}], 10):
            ws.send_text(json.dumps({**msg, "id": i}))
            a = _until(ws, "ack")
            assert a["id"] == i and a["ok"] and a["note"], a
        ws.send_text(json.dumps({"type": "protocol", "name": "boom", "id": 20}))    # the Mac side raises
        a = _until(ws, "ack")
        assert a["id"] == 20 and a["ok"] is False and "didn't work" in a["note"]
        ws.send_text(json.dumps({"type": "stop", "id": 21}))                 # ...and the link still works
        assert _until(ws, "ack")["id"] == 21
        ws.send_text("not json")
        ws.send_text(json.dumps({"type": "stop", "id": 22}))
        assert _until(ws, "ack")["id"] == 22


def test_voice_needs_the_secure_connection(remote):
    client, store, got = remote
    token = _paired(client, store)
    with client.websocket_connect(f"/remote/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "voice", "mode": "talk", "id": 1}))
        a = _until(ws, "ack")
        assert a["ok"] is False and "secure" in a["note"]
        ws.send_bytes(b"\x00\x01" * 800)                                   # stray audio is ignored safely
        ws.send_text(json.dumps({"type": "stop", "id": 2}))
        assert _until(ws, "ack")["id"] == 2


def test_his_voice_is_sent_to_the_phone_that_asked(remote):
    import numpy as np

    client, store, got = remote
    token = _paired(client, store)
    with client.websocket_connect(f"/remote/ws?token={token}") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "text", "text": "Time?", "speak": True, "id": 1}))
        _until(ws, "ack")
        route = got[-1]["route"]
        assert callable(route)
        tone = (0.2 * np.sin(np.linspace(0, 400, 24000))).astype(np.float32)
        assert route(tone, 24000, "It's nine.") is True
        meta = _until(ws, "audio")
        assert meta["text"] == "It's nine." and meta["duration"] == 1.0
        wav = ws.receive_bytes()
        assert wav[:4] == b"RIFF" and len(wav) == meta["bytes"] and len(wav) > 48000
    time.sleep(0.2)
    assert route(tone, 24000, "x") is False                                  # phone gone: the Mac speaks instead


def test_your_tailscale_network_counts_as_yours_the_internet_does_not():
    """Away from home the phone reaches the Mac through Tailscale (100.64.0.0/10); nothing else from
    outside the home network gets in."""
    from jarvis.remote.server import private_client

    for ok in ("100.101.2.3", "100.64.0.1", "100.127.255.254", "fd7a:115c:a1e0::5", "192.168.1.4", "127.0.0.1"):
        assert private_client(ok), ok
    for bad in ("8.8.8.8", "100.128.0.1", "100.63.255.255", "2606:4700::1111"):
        assert not private_client(bad), bad


def test_tailscale_address_from_its_status(monkeypatch):
    import json
    import subprocess
    from types import SimpleNamespace

    from jarvis.remote import server

    status = {"Self": {"Online": True, "DNSName": "mac-mini.tail1234.ts.net.", "TailscaleIPs": ["100.101.2.3", "fd7a::1"]}}
    monkeypatch.setattr(server.os.path if hasattr(server, "os") else __import__("os").path, "exists",
                        lambda p: p == server.TAILSCALE_CLI[0])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=json.dumps(status)))
    assert server.tailscale_address() == ("mac-mini.tail1234.ts.net", "100.101.2.3")


def test_the_phone_gets_the_conversation_so_far(remote):
    """On connecting, the phone receives what was said recently (both sides) for its history."""
    from jarvis.events import bus

    client, store, got = remote
    bus.history.append({"type": "transcript", "text": "What's on today?", "ts": 1.0})
    bus.history.append({"type": "reply", "text": "Two meetings, the first at ten.", "ts": 2.0})
    token = _paired(client, store)
    with client.websocket_connect(f"/remote/ws?token={token}") as ws:
        hist = _until(ws, "history")
    texts = [(h["type"], h["text"]) for h in hist["items"]]
    assert ("transcript", "What's on today?") in texts and ("reply", "Two meetings, the first at ten.") in texts
