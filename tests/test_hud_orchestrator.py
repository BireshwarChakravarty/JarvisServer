import asyncio

from fakes import FakeClient, message, text_block, tool_block
from fastapi.testclient import TestClient

from jarvis.events import bus


def test_hud_serves_and_streams(cfg):
    from jarvis.hud.server import create_app

    client = TestClient(create_app(None))
    r = client.get("/")
    assert r.status_code == 200 and "J.A.R.V.I.S." in r.text
    for asset in ("hud.js", "hud.css", "reactor.js"):
        assert client.get(f"/static/{asset}").status_code == 200
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
    assert client.get("/camera.mjpg").status_code == 404


def test_orchestrator_text_turn_and_confirmation(cfg, monkeypatch):
    from jarvis.core.orchestrator import Jarvis

    async def run():
        app = Jarvis(cfg, headless=True)
        await app.start()
        # a scripted turn that tries to send a message (a confirm-gated tool)
        app.brain._client = FakeClient([
            message([tool_block("m1", "send_message", {"recipient": "Pepper", "text": "On my way"})],
                    stop_reason="tool_use"),
            message([text_block("Message sent, sir.")]),
        ])
        import jarvis.mac.pim as pim

        sent = []
        monkeypatch.setattr(pim, "send_imessage", lambda r, t: sent.append((r, t)) or "sent")
        q = bus.subscribe()

        async def approve_when_asked():
            while True:
                e = await q.get()
                if e["type"] == "confirm":
                    assert "Pepper" in e["action"]
                    await app.hud_command({"type": "confirm", "approved": True})
                    return

        approver = asyncio.create_task(approve_when_asked())
        reply = await asyncio.wait_for(app.handle_text("Tell Pepper I'm on my way", source="hud"), 10)
        await approver
        return reply, sent

    reply, sent = asyncio.run(run())
    assert sent == [("Pepper", "On my way")]
    assert reply == "Message sent, sir."


def test_greeting_falls_back_offline(cfg):
    from jarvis.core.orchestrator import Jarvis

    async def run():
        app = Jarvis(cfg, headless=True)
        await app.start()

        class Down(FakeClient):
            pass

        import anthropic
        import httpx2

        def fail(**params):
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))

        client = FakeClient([])
        client.beta.messages.stream = fail
        app.brain._client = client
        q = bus.subscribe()
        await app.greet("login")
        replies = []
        while not q.empty():
            e = q.get_nowait()
            if e["type"] == "reply":
                replies.append(e["text"])
        return replies

    replies = asyncio.run(run())
    assert any(r.startswith(("Good", "Burning", "Still up", "The rest")) for r in replies)


def test_a_failing_hud_command_keeps_the_link(cfg):
    from types import SimpleNamespace

    from jarvis.hud.server import create_app

    got = []

    async def hud_command(msg):
        if msg.get("type") == "boom":
            raise RuntimeError("kaboom")
        got.append(msg["type"])
    j = SimpleNamespace(hud_command=hud_command, capabilities={}, protocols=SimpleNamespace(list=lambda: []),
                        _refresh_panels=lambda: asyncio.sleep(0), cfg=cfg, camera=None)
    client = TestClient(create_app(j))
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text('{"type": "boom"}')
        ws.send_text('["not", "a", "command"]')
        ws.send_text('{"type": "listen"}')
        for _ in range(50):
            if got:
                break
            import time
            time.sleep(0.02)
    assert got == ["listen"]
