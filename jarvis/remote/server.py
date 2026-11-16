"""The iPhone / iPad companion: a small web app on your home network, paired by QR code.

It runs on its own port, separate from the HUD (which stays private to this Mac), and:
  • answers only devices on a private network (home Wi-Fi), never the internet
  • needs a per-device key for everything except the page itself and the pairing call
  • forwards only what the companion shows (state, what he's saying, confirmations, agenda,
    weather, protocols); never the camera, faces or memory
  • accepts only a short list of commands (ask, stop, answer yes/no, protocols, mute,
    hand control, lock, show on Mac)
Keys travel in the page, not in cookies, so other websites can't borrow them.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import subprocess
from pathlib import Path

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jarvis.events import bus

log = logging.getLogger("jarvis.remote")
STATIC = Path(__file__).parent.parent / "hud" / "static"
PORT = 7778

FORWARD = {"hello", "state", "speaking", "reply", "transcript", "confirm", "confirm_result", "agenda", "weather",
           "protocols", "hand_control", "mic", "announce", "error", "interrupted", "level"}
REPLAY = {"agenda", "weather", "protocols", "hand_control", "mic", "state"}
COMMANDS = {"text", "stop", "confirm", "protocol", "mute", "hand_control", "listen", "show", "lock"}


def private_client(host: str | None) -> bool:
    try:
        ip = ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return host == "testclient"
    return ip.is_private or ip.is_loopback or ip.is_link_local


def lan_addresses() -> tuple[str, str | None]:
    """(Bonjour name like 'Tonys-Mac-mini.local', LAN IP) for the pairing link."""
    name = None
    try:
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                              timeout=3).stdout.strip() or None
    except Exception:
        pass
    name = (name or socket.gethostname().split(".")[0]) + ".local"
    ip = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.255.255", 1))      # no packet is sent; just picks the LAN interface
            ip = s.getsockname()[0]
    except Exception:
        pass
    return name, ip


def create_remote_app(supervisor, store) -> FastAPI:
    app = FastAPI(title="JARVIS companion", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def lan_only(request, call_next):
        if not private_client(request.client.host if request.client else None):
            return JSONResponse({"error": "home network only"}, status_code=403)
        return await call_next(request)

    app.mount("/remote/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    @app.get("/remote")
    async def page():
        return FileResponse(STATIC / "remote.html", headers={"Cache-Control": "no-store"})

    @app.get("/remote/manifest.webmanifest")
    async def manifest():
        return JSONResponse({
            "name": "JARVIS", "short_name": "JARVIS", "start_url": "/remote", "display": "standalone",
            "background_color": "#030508", "theme_color": "#030508",
            "icons": [{"src": "/remote/static/vendor/icon-192.png", "sizes": "192x192", "type": "image/png"}],
        }, media_type="application/manifest+json")

    @app.post("/remote/api/pair")
    async def pair(body: dict = Body(...)):
        got = store.pair(str(body.get("code", "")), str(body.get("name", "")))
        if not got:
            return JSONResponse({"ok": False, "error": "That code has expired. Show a new QR code on the Mac."},
                                status_code=403)
        device_id, token = got
        log.info("paired device %s (%s)", device_id, body.get("name"))
        bus.emit("announce", text=f"Paired {str(body.get('name') or 'a new device')[:40]}.", priority="normal")
        return {"ok": True, "device": device_id, "token": token}

    @app.websocket("/remote/ws")
    async def ws(socket: WebSocket):
        if not private_client(socket.client.host if socket.client else None):
            await socket.close(code=4403)
            return
        device = store.check(socket.query_params.get("token"))
        if not device:
            await socket.close(code=4401)
            return
        await socket.accept()
        q = bus.subscribe()
        cfg = supervisor.cfg
        try:
            await socket.send_json({"type": "hello", "owner": cfg.get("owner.name", ""),
                                    "title": cfg.get("owner.title", "sir"), "device": device,
                                    "state": bus.state.get("state", "idle")})
            latest: dict[str, dict] = {}
            for e in list(bus.history):
                if e.get("type") in REPLAY:
                    latest[e["type"]] = e
            for e in latest.values():
                await socket.send_json({**e, "replay": True})

            async def pump():
                while True:
                    e = await q.get()
                    if e.get("type") in FORWARD:
                        await socket.send_json(e)

            async def listen():
                while True:
                    msg = json.loads(await socket.receive_text())
                    await command(msg)

            await asyncio.gather(pump(), listen())
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:
            log.debug("companion socket closed: %s", exc)
        finally:
            bus.unsubscribe(q)

    async def command(msg: dict) -> None:
        kind = msg.get("type")
        if kind not in COMMANDS:
            return
        j = supervisor.jarvis
        if kind == "show":
            supervisor.show_window()
            return
        if j is None:
            return
        if kind == "lock":
            from jarvis.tools.registry import registry
            await registry.call("system_action", {"action": "lock_screen"}, j.ctx)
            return
        if kind == "text":
            msg = {"type": "text", "text": str(msg.get("text", ""))[:2000]}
        await j.hud_command(msg)

    return app


class RemoteServer:
    """Start/stop the companion listener at runtime (Settings toggle)."""

    def __init__(self, supervisor, store) -> None:
        self.supervisor, self.store = supervisor, store
        self._server = None
        self._task: asyncio.Task | None = None
        self.error = ""

    @property
    def running(self) -> bool:
        return self._server is not None and self._task is not None and not self._task.done()

    async def start(self, host: str = "0.0.0.0", port: int = PORT) -> bool:
        if self.running:
            return True
        import uvicorn

        config = uvicorn.Config(create_remote_app(self.supervisor, self.store), host=host, port=port,
                                log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None
        self._task = asyncio.ensure_future(self._server.serve())
        for _ in range(50):
            await asyncio.sleep(0.1)
            if self._server.started:
                log.info("companion listening on port %s", port)
                self.error = ""
                return True
            if self._task.done():
                break
        self.error = f"couldn't open port {port} (is something else using it?)"
        log.warning("companion: %s", self.error)
        self._server = None
        return False

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            try:
                await asyncio.wait_for(self._task, 5)
            except Exception:
                pass
        self._server, self._task = None, None
