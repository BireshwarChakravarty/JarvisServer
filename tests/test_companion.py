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


@pytest.fixture
def remote(tmp_path, cfg):
    store = DeviceStore(tmp_path / "companion.json")
    got = []

    async def hud_command(msg):
        got.append(msg)

    sup = SimpleNamespace(cfg=cfg, jarvis=SimpleNamespace(hud_command=hud_command, ctx=None),
                          show_window=lambda url=None: got.append({"type": "shown"}))
    return TestClient(create_remote_app(sup, store)), store, got


def test_page_pair_and_live_link(remote):
    client, store, got = remote
    assert client.get("/remote").status_code == 200 and "remote.js" in client.get("/remote").text
    assert client.get("/remote/manifest.webmanifest").json()["display"] == "standalone"
    with pytest.raises(WebSocketDisconnect):                                # no key: refused
        with client.websocket_connect("/remote/ws") as ws:
            ws.receive_json()
    assert client.post("/remote/api/pair", json={"code": "guess", "name": "x"}).status_code == 403
    code, _ = store.current_code()
    token = client.post("/remote/api/pair", json={"code": code, "name": "iPhone"}).json()["token"]
    with client.websocket_connect(f"/remote/ws?token={token}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["device"]
        ws.send_text(json.dumps({"type": "text", "text": "Lights on"}))
        ws.send_text(json.dumps({"type": "confirm", "approved": True}))
        ws.send_text(json.dumps({"type": "shell", "command": "rm -rf /"}))  # not on the list: ignored
        ws.send_text(json.dumps({"type": "show"}))
        time.sleep(0.3)
    kinds = [m["type"] for m in got]
    assert kinds == ["text", "confirm", "shown"] and got[0]["text"] == "Lights on"
    assert "shell" not in COMMANDS
