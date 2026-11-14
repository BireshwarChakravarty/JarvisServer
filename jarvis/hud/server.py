"""HUD server: serves the holographic interface, streams every JARVIS event over a
WebSocket, relays commands back, and serves the camera as MJPEG."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from jarvis.events import bus

log = logging.getLogger("jarvis.hud")
STATIC = Path(__file__).parent / "static"


def create_app(jarvis=None) -> FastAPI:
    app = FastAPI(title="J.A.R.V.I.S. HUD", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/status")
    async def status():
        cfg = jarvis.cfg if jarvis else None
        return JSONResponse({
            "state": bus.state.get("state"),
            "owner": cfg.get("owner.name") if cfg else "",
            "title": cfg.get("owner.title") if cfg else "sir",
            "capabilities": getattr(jarvis, "capabilities", {}),
        })

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        q = bus.subscribe()
        cfg = jarvis.cfg if jarvis else None
        hello = {
            "type": "hello",
            "owner": cfg.get("owner.name", "") if cfg else "",
            "title": cfg.get("owner.title", "sir") if cfg else "sir",
            "city": cfg.get("location.city", "") if cfg else "",
            "state": bus.state.get("state"),
            "capabilities": getattr(jarvis, "capabilities", {}),
            "history": [e for e in bus.history if e["type"] in (
                "boot", "transcript", "reply", "tool", "announce", "weather", "agenda", "alarms",
                "protocols", "briefing", "capabilities", "usage")][-120:],
            "camera": bool(jarvis and jarvis.camera and jarvis.camera.available),
            "protocols": jarvis.protocols.list() if jarvis else [],
            "ts": time.time(),
        }
        await socket.send_text(json.dumps(hello, default=str))
        if jarvis is not None:
            asyncio.create_task(jarvis._refresh_panels())

        async def pump():
            while True:
                event = await q.get()
                await socket.send_text(json.dumps(event, default=str))

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                raw = await socket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if jarvis is not None:
                    await jarvis.hud_command(msg)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover
            log.debug("ws closed: %s", exc)
        finally:
            pump_task.cancel()
            bus.unsubscribe(q)

    @app.get("/camera.mjpg")
    async def camera():
        cam = jarvis.camera if jarvis else None
        if cam is None or not cam.available:
            return JSONResponse({"error": "camera unavailable"}, status_code=404)

        async def frames():
            boundary = b"--frame\r\n"
            while cam.available:
                data = await asyncio.to_thread(cam.jpeg, 70, 640)
                if data:
                    yield boundary + b"Content-Type: image/jpeg\r\nContent-Length: " + \
                        str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n"
                await asyncio.sleep(1 / 12)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    return app


async def serve(app: FastAPI, host: str, port: int) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # let the main app own signals
    await server.serve()
