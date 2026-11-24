"""The local server refuses other websites: no WebSocket hijacking, no cross-site POSTs, no DNS rebinding."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def client(cfg):
    from jarvis.app.supervisor import Supervisor
    from jarvis.hud.server import create_app

    return TestClient(create_app(supervisor=Supervisor(cfg)), base_url="http://127.0.0.1:7777")


def test_own_pages_and_native_clients_work(client):
    assert client.get("/setup").status_code == 200
    assert client.post("/api/setup/voice", json={"voice": "lewis"},
                       headers={"Origin": "http://127.0.0.1:7777"}).status_code == 200
    assert client.post("/api/setup/voice", json={"voice": "lewis"}).status_code == 200   # no Origin: native
    with client.websocket_connect("/ws", headers={"Origin": "http://127.0.0.1:7777", "Host": "127.0.0.1:7777"}) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_other_websites_are_refused(client):
    evil = {"Origin": "https://evil.example"}
    assert client.post("/api/setup/voice", json={"voice": "lewis"}, headers=evil).status_code == 403
    assert client.post("/api/app/show", headers={"Origin": "http://127.0.0.1:9999"}).status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers=evil) as ws:
            ws.receive_json()
    # an <img src="http://127.0.0.1:7777/camera.mjpg"> on another site
    assert client.get("/camera.mjpg", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # DNS rebinding: evil.example resolving to 127.0.0.1
    assert client.get("/api/status", headers={"Host": "evil.example:7777"}).status_code == 403
    assert client.post("/api/setup/voice", json={"voice": "x"}, headers={"Origin": "null"}).status_code == 403
