"""The iPhone / iPad companion: a small web app on your home network, paired by QR code.

It runs on its own ports, separate from the HUD (which stays private to this Mac), and:
  • answers only devices on a private network (home Wi-Fi), never the internet
  • needs a per-device key for everything except the page itself, pairing and the certificate
  • forwards only what the companion shows (state, what he's saying, confirmations, agenda,
    weather, protocols); never the camera, faces or memory
  • accepts only a short list of commands (ask, stop, answer yes/no, protocols, mute,
    hand control, lock, show on Mac, voice)
  • answers every command with an acknowledgement, so the phone can show that it worked (or why not)

Two listeners: plain http on 7778 (pairing, and the certificate for voice) and https on 7779,
which iOS requires before Safari lets the page use the microphone (see jarvis/remote/tls.py).
Keys travel in the page, not in cookies, so other websites can't borrow them.
"""
from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import logging
import socket
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np
from fastapi import Body, FastAPI, WebSocket
from fastapi.responses import JSONResponse, Response

from jarvis.core import themes
from jarvis.hud import assets
from jarvis.hud.assets import RevalidatedStatic
from jarvis.events import bus

log = logging.getLogger("jarvis.remote")
STATIC = Path(__file__).parent.parent / "hud" / "static"
PORT = 7778
SECURE_PORT = 7779

FORWARD = {"hello", "state", "speaking", "reply", "transcript", "confirm", "confirm_result", "agenda", "weather",
           "protocols", "hand_control", "mic", "announce", "error", "interrupted", "level", "theme", "prefs"}
REPLAY = {"agenda", "weather", "protocols", "hand_control", "mic", "state"}
COMMANDS = {"text", "stop", "confirm", "protocol", "mute", "hand_control", "listen", "show", "lock", "voice", "ping"}


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


def wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm)
    return out.getvalue()


class Link:
    """One open companion connection."""

    _count = 0

    def __init__(self, device: str, secure: bool) -> None:
        self.device, self.secure = device, secure
        Link._count += 1
        self.order = Link._count                    # newer links win (a reconnect leaves an old one dying)
        self.out: asyncio.Queue = asyncio.Queue(maxsize=600)
        self.voice = None

    def put(self, item) -> None:
        if self.out.full():                         # a stalled phone mustn't hold up JARVIS
            try:
                self.out.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.out.put_nowait(item)


def create_remote_app(supervisor, store, certs=None) -> FastAPI:
    app = FastAPI(title="JARVIS companion", docs_url=None, redoc_url=None, openapi_url=None)
    links: dict[str, set[Link]] = {}
    app.state.links = links
    wake_models: dict[str, object] = {}
    wake_lock = threading.Lock()

    @app.middleware("http")
    async def lan_only(request, call_next):
        if not private_client(request.client.host if request.client else None):
            return JSONResponse({"error": "home network only"}, status_code=403)
        return await call_next(request)

    app.mount("/remote/static", RevalidatedStatic(directory=STATIC), name="static")

    @app.get("/")
    @app.get("/remote")
    async def page():
        return assets.page(STATIC / "remote.html", STATIC, headers={"Cache-Control": "no-store"})

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

    # ── secure (voice) setup ─────────────────────────────────────────────
    def secure_port() -> int | None:
        r = getattr(supervisor, "remote", None)
        return getattr(r, "secure_port", SECURE_PORT) if getattr(r, "secure_running", False) else None

    @app.get("/remote/api/secure")
    async def secure_info():
        port = secure_port() if certs is not None else None
        return {"available": port is not None, "port": port or SECURE_PORT,
                "name": f"JARVIS on {certs.mac_name}" if certs is not None else "JARVIS"}

    @app.get("/remote/api/ping")
    async def ping():
        # the plain page probes the secure port with this to learn whether the certificate is trusted
        return JSONResponse({"ok": True}, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

    @app.get("/remote/jarvis.mobileconfig")
    async def profile():
        if certs is None:
            return JSONResponse({"error": "not available"}, status_code=404)
        data = await asyncio.to_thread(certs.mobileconfig)
        return Response(data, media_type="application/x-apple-aspen-config",
                        headers={"Content-Disposition": 'attachment; filename="JARVIS.mobileconfig"',
                                 "Cache-Control": "no-store"})

    # ── the live link ────────────────────────────────────────────────────
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
        link = Link(device, secure=socket.url.scheme == "wss")
        links.setdefault(device, set()).add(link)
        q = bus.subscribe()
        cfg = supervisor.cfg
        j = supervisor.jarvis
        link.put({"type": "hello", "owner": cfg.get("owner.name", ""), "title": cfg.get("owner.title", "sir"),
                  "device": device, "state": bus.state.get("state", "idle"), "theme": themes.current(cfg), "clock24": themes.clock_24h(cfg),
                  "voice": {"secure": link.secure, "stt": bool(j is not None and getattr(j, "stt", None)),
                            "secure_port": secure_port()}})
        latest: dict[str, dict] = {}
        for e in list(bus.history):
            if e.get("type") in REPLAY:
                latest[e["type"]] = e
        for e in latest.values():
            link.put({**e, "replay": True})

        async def forward():
            while True:
                e = await q.get()
                if e.get("type") in FORWARD:
                    link.put(e)

        async def writer():
            while True:
                item = await link.out.get()
                if isinstance(item, dict):
                    await socket.send_text(json.dumps(item, default=str))
                elif isinstance(item, tuple) and item[0] == "audio":
                    await socket.send_text(json.dumps(item[1]))
                    await socket.send_bytes(item[2])

        async def reader():
            while True:
                msg = await socket.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                if msg.get("bytes"):
                    if link.voice is not None:
                        link.voice.feed(msg["bytes"])
                    continue
                try:
                    data = json.loads(msg.get("text") or "{}")
                except ValueError:
                    continue
                if isinstance(data, dict):
                    await respond(link, data)

        tasks = [asyncio.ensure_future(t()) for t in (forward, writer, reader)]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            if link.voice is not None:
                link.voice.stop()
            bus.unsubscribe(q)
            links.get(device, set()).discard(link)
            try:
                await socket.close()
            except Exception:
                pass

    async def respond(link: Link, msg: dict) -> None:
        kind = msg.get("type")
        if kind not in COMMANDS:
            return
        if kind == "ping":
            link.put({"type": "pong", "t": msg.get("t")})
            return
        try:
            ok, note = await command(link, msg)
        except Exception as exc:              # a failing command must never take the link down
            log.exception("companion command %s failed", kind)
            ok, note = False, f"That didn't work on the Mac ({type(exc).__name__})."
        if msg.get("id") is not None:
            link.put({"type": "ack", "id": msg.get("id"), "for": kind, "ok": ok, "note": note})

    async def command(link: Link, msg: dict) -> tuple[bool, str]:
        kind = msg.get("type")
        if kind == "show":
            supervisor.show_window()          # returns at once (the window work runs on its own thread)
            return True, "JARVIS is on your Mac's screen"
        j = supervisor.jarvis
        if j is None:
            return False, "JARVIS is still starting on your Mac."
        if kind == "lock":
            from jarvis.mac.system import lock_screen

            await asyncio.to_thread(lock_screen)
            return True, "Mac locked"
        if kind == "text":
            text = str(msg.get("text", "")).strip()[:2000]
            if not text:
                return False, "Nothing to send."
            j.queue_request(text, source="phone", route=route_for(link.device) if msg.get("speak") else None)
            return True, "Sent"
        if kind == "voice":
            return voice(link, msg)
        if kind == "stop" and link.voice is not None and link.voice.mode == "talk":
            link.voice.stop()
            link.put({"type": "voice", "phase": "off"})
        await j.hud_command(msg)
        notes = {"stop": "Stopped", "confirm": "Answered", "listen": "Listening on the Mac",
                 "protocol": f"Running {str(msg.get('name', '')).replace('_', ' ')}",
                 "mute": "Mac microphone muted" if msg.get("muted") else "Mac microphone on",
                 "hand_control": "Hand control on" if msg.get("on") else "Hand control off"}
        return True, notes.get(kind, "Done")

    def voice(link: Link, msg: dict) -> tuple[bool, str]:
        from jarvis.remote.voice import PhoneVoice

        mode = str(msg.get("mode", "off"))
        if mode != "off" and not link.secure:
            return False, "Voice needs the secure connection. Set it up once from the mic button."
        if link.voice is None:
            loop = asyncio.get_running_loop()

            def send(m: dict, link=link) -> None:
                loop.call_soon_threadsafe(link.put, m)
            link.voice = PhoneVoice(supervisor.jarvis, link.device, send,
                                    route_factory=lambda d=link.device: route_for(d), wake_factory=load_wake)
        err = link.voice.set_mode(mode, speak_here=msg.get("speak", True))
        if err:
            return False, err
        return True, {"talk": "Listening", "wake": "Say “Hey JARVIS”", "off": "Voice off"}[mode]

    def load_wake():
        """A wake-word detector for phone audio (its own state: it never mixes with the Mac's mic)."""
        with wake_lock:
            if "w" not in wake_models:
                j = supervisor.jarvis
                from jarvis.voice.audio import WakeWord

                mac = getattr(j, "wake", None)
                w = WakeWord(getattr(mac, "model_name", "hey_jarvis"), getattr(mac, "threshold", 0.5),
                             getattr(mac, "models_dir", None), announce=False)
                wake_models["w"] = w if w.load() else None
            return wake_models["w"]

    def route_for(device: str):
        """A speaker route that plays his voice on this device (its newest open link)."""
        loop = asyncio.get_running_loop()
        counter = {"n": 0}

        def sink(audio, sr, text) -> bool:
            live = list(links.get(device, ()))
            if not live:
                return False
            counter["n"] += 1
            data = wav_bytes(audio, sr)
            meta = {"type": "audio", "n": counter["n"], "text": text, "duration": round(len(audio) / max(1, sr), 2),
                    "bytes": len(data)}
            # the link that's listening to you, else the newest (a set has no order of its own)
            target = max(live, key=lambda ln: (ln.voice is not None and ln.voice.mode != "off", ln.order))
            loop.call_soon_threadsafe(target.put, ("audio", meta, data))
            return True
        return sink

    app.state.route_for = route_for
    return app


class RemoteServer:
    """Start/stop the companion listeners at runtime (Settings toggle)."""

    def __init__(self, supervisor, store) -> None:
        self.supervisor, self.store = supervisor, store
        self._servers: list = []
        self._tasks: list[asyncio.Task] = []
        self.error = ""
        self.secure_error = ""
        self.secure_running = False
        self.secure_port = SECURE_PORT
        self.certs = None

    @property
    def running(self) -> bool:
        return bool(self._tasks) and not self._tasks[0].done()

    async def _serve(self, app, host: str, port: int, **ssl) -> bool:
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False,
                                               ws_max_size=4 * 1024 * 1024, **ssl))
        server.install_signal_handlers = lambda: None
        task = asyncio.ensure_future(server.serve())
        for _ in range(150):        # up to 15 s: at login the Mac is busy loading models
            await asyncio.sleep(0.1)
            if server.started:
                self._servers.append(server)
                self._tasks.append(task)
                return True
            if task.done():
                break
        server.should_exit = True
        return False

    async def start(self, host: str = "0.0.0.0", port: int = PORT, secure_port: int | None = SECURE_PORT) -> bool:
        if self.running:
            return True
        from jarvis.remote.tls import Certificates

        name, ip = await asyncio.to_thread(lan_addresses)
        home = getattr(self.supervisor.cfg, "home", None)
        self.certs = Certificates(Path(home) / "companion-tls", name.removesuffix(".local").replace("-", " ")) \
            if home is not None else None
        app = create_remote_app(self.supervisor, self.store, self.certs)
        self.app = app
        if not await self._serve(app, host, port):
            self.error = f"couldn't open port {port} (is something else using it?)"
            log.warning("companion: %s", self.error)
            return False
        self.error = ""
        log.info("companion listening on port %s", port)
        self.secure_running = False
        self.secure_port = secure_port or SECURE_PORT
        if secure_port and self.certs is not None:
            for attempt in (1, 2):
                try:
                    pem, key = await asyncio.to_thread(self.certs.ensure_server, [name], [ip] if ip else [])
                    if await self._serve(app, host, secure_port, ssl_certfile=str(pem), ssl_keyfile=str(key)):
                        self.secure_running, self.secure_error = True, ""
                        log.info("companion (secure, for voice) listening on port %s", secure_port)
                        break
                    self.secure_error = f"couldn't open port {secure_port}"
                except Exception as exc:
                    self.secure_error = f"{type(exc).__name__}: {exc}"
                log.warning("companion voice: attempt %d failed: %s", attempt, self.secure_error)
                await asyncio.sleep(1)
            if self.secure_error:
                log.warning("companion voice unavailable: %s", self.secure_error)
        return True

    async def stop(self) -> None:
        for s in self._servers:
            s.should_exit = True
        for t in self._tasks:
            try:
                await asyncio.wait_for(t, 5)
            except Exception:
                pass
        self._servers, self._tasks = [], []
        self.secure_running = False
