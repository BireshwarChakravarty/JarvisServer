"""HUD + onboarding server: serves the interface, streams every JARVIS event over a
WebSocket, relays commands back, and serves the camera as MJPEG."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from jarvis.aio import spawn
from jarvis.events import bus

log = logging.getLogger("jarvis.hud")
STATIC = Path(__file__).parent / "static"
NO_STORE = {"Cache-Control": "no-store"}


def create_app(jarvis=None, supervisor=None) -> FastAPI:
    app = FastAPI(title="J.A.R.V.I.S.", docs_url=None, redoc_url=None)
    from jarvis.hud.guard import LocalOnly

    app.add_middleware(LocalOnly)   # other websites can't drive JARVIS through 127.0.0.1
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def J():
        return supervisor.jarvis if supervisor is not None else jarvis

    def CFG():
        if supervisor is not None:
            return supervisor.cfg
        return jarvis.cfg if jarvis is not None else None

    def CAMERA():
        j = J()
        if j is not None and j.camera is not None:
            return j.camera
        return supervisor.camera if supervisor is not None else None

    @app.get("/")
    async def index():
        cfg = CFG()
        if supervisor is not None and cfg is not None and not cfg.setup_complete:
            return RedirectResponse("/setup")
        return FileResponse(STATIC / "index.html", headers=NO_STORE)

    @app.get("/setup")
    async def setup_page():
        return FileResponse(STATIC / "onboarding.html", headers=NO_STORE)

    @app.get("/pair")
    async def pair_page():
        return FileResponse(STATIC / "pair.html", headers=NO_STORE)

    @app.get("/mini")
    async def mini_page():
        return FileResponse(STATIC / "mini.html", headers=NO_STORE)

    @app.get("/guide")
    async def guide_page():
        return FileResponse(STATIC / "guide.html", headers=NO_STORE)

    @app.get("/api/status")
    async def status():
        cfg = CFG()
        j = J()
        return JSONResponse({
            "state": bus.state.get("state"),
            "owner": cfg.get("owner.name") if cfg else "",
            "title": cfg.get("owner.title") if cfg else "sir",
            "capabilities": getattr(j, "capabilities", {}),
            "running": j is not None,
        })

    if supervisor is not None:
        from jarvis.app.setup_api import make_router

        app.include_router(make_router(supervisor))

        @app.post("/api/app/restart")
        async def restart():
            exe = sys.executable or ""
            if ".app/Contents/" in exe:
                bundle = exe.split(".app/Contents/")[0] + ".app"

                def go():
                    time.sleep(0.5)
                    subprocess.Popen(["/bin/sh", "-c", f'sleep 1; /usr/bin/open -n "{bundle}"'])
                    os._exit(0)

                asyncio.get_running_loop().run_in_executor(None, go)
                return {"ok": True}
            return JSONResponse({"ok": False, "error": "restart JARVIS manually"}, status_code=400)

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        q = bus.subscribe()
        cfg = CFG()
        j = J()
        hello = {
            "type": "hello",
            "owner": cfg.get("owner.name", "") if cfg else "",
            "title": cfg.get("owner.title", "sir") if cfg else "sir",
            "city": cfg.get("location.city", "") if cfg else "",
            "state": bus.state.get("state"),
            "capabilities": getattr(j, "capabilities", {}),
            "history": [e for e in bus.history if e["type"] in (
                "boot", "transcript", "reply", "tool", "announce", "weather", "agenda", "alarms",
                "protocols", "briefing", "capabilities", "usage")][-120:],
            "camera": bool(CAMERA() is not None and CAMERA().available),
            "protocols": j.protocols.list() if j else [],
            "running": j is not None,
            "ts": time.time(),
        }
        await socket.send_text(json.dumps(hello, default=str))
        if j is not None:
            spawn(j._refresh_panels())

        async def pump():
            while True:
                event = await q.get()
                await socket.send_text(json.dumps(event, default=str))

        pump_task = asyncio.ensure_future(pump())
        try:
            while True:
                raw = await socket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") == "settings" and supervisor is not None:
                    supervisor.show_window("/setup")
                    continue
                if J() is not None:
                    try:
                        await J().hud_command(msg)
                    except Exception:            # one bad command must never drop the HUD's link
                        log.exception("HUD command %s failed", msg.get("type"))
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover
            log.debug("ws closed: %s", exc)
        finally:
            pump_task.cancel()
            bus.unsubscribe(q)

    @app.get("/camera.mjpg")
    async def camera():
        cam = CAMERA()
        if cam is None or not cam.available:
            return JSONResponse({"error": "camera unavailable"}, status_code=404)

        async def frames():
            boundary = b"--frame\r\n"
            while cam.available:
                data = await asyncio.to_thread(cam.jpeg, 72, 720)
                if data:
                    yield boundary + b"Content-Type: image/jpeg\r\nContent-Length: " + \
                        str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n"
                await asyncio.sleep(1 / 15)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    return app


async def serve(app: FastAPI, host: str, port: int) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # let the main app own signals
    await server.serve()
