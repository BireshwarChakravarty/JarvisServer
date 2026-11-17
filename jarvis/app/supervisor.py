"""The app's supervisor: owns everything before JARVIS proper is running (onboarding,
model downloads, the setup camera/mic) and starts the orchestrator when setup is done."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.events import bus
from jarvis.models import ModelManager

log = logging.getLogger("jarvis.app")

ESSENTIAL = ("wake", "ears", "voice")


class Supervisor:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.jarvis = None
        self.mini = None   # the floating mini JARVIS (app mode only)
        from jarvis.remote.devices import DeviceStore
        from jarvis.remote.server import RemoteServer

        self.devices = DeviceStore(cfg.home / "companion.json")
        self.remote = RemoteServer(self, self.devices)   # iPhone / iPad companion (off until enabled)
        self.models = ModelManager(cfg.models_dir, cfg.get("voice.stt.model", "small.en"))
        self.camera = None          # setup-time camera (released before JARVIS starts)
        self.faces = None           # setup-time face engine
        self.speaker = None         # setup-time voice
        self.window = None          # pywebview window, set by the runner
        self.guide_overlay = None   # on-screen guidance layer, set by the runner
        self.loop: asyncio.AbstractEventLoop | None = None
        self._downloads: asyncio.Task | None = None
        self._jarvis_task: asyncio.Task | None = None
        self.busy: dict[str, bool] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def boot(self) -> None:
        self.loop = asyncio.get_running_loop()
        bus.bind(self.loop)
        if self.cfg.get("app.companion", False):
            await self.remote.start()
        if self.cfg.setup_complete:
            if not all(self.models.ready(k) for k in ESSENTIAL):
                self.start_downloads()
                await self._downloads
            await self.start_jarvis(greet=True)
        else:
            bus.emit("state", state="idle")
            self.start_downloads()   # start fetching models while you fill in the forms

    def start_downloads(self) -> None:
        if self._downloads is None or self._downloads.done():
            self._downloads = asyncio.ensure_future(self.models.ensure_all(["wake", "ears", "voice", "eyes"]))

    async def start_jarvis(self, greet: bool = True) -> None:
        if self.jarvis is not None:
            return
        self.release_setup_devices()
        from jarvis.config import load_config
        from jarvis.core.orchestrator import Jarvis

        self.cfg = load_config()
        self.jarvis = Jarvis(self.cfg)
        self.jarvis.show_window = self.show_window   # flick ↑ brings the HUD forward
        if getattr(self, "guide_overlay", None) is not None and self.jarvis.guide is not None:
            from jarvis.app.guide_overlay import mouse_state

            self.jarvis.guide.overlay = self.guide_overlay
            self.jarvis.guide.mouse = mouse_state
        self._jarvis_task = asyncio.ensure_future(self.jarvis.run(greet=greet))
        self._jarvis_task.add_done_callback(self._jarvis_done)
        log.info("JARVIS started")

    def _jarvis_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("JARVIS stopped: %r", exc)
            bus.emit("error", message=f"JARVIS core stopped: {exc}")

    # ── setup-time devices ────────────────────────────────────────────────
    def setup_camera(self):
        if self.camera is None or not self.camera.available:
            from jarvis.vision.camera import Camera

            self.camera = Camera(int(self.cfg.get("vision.camera_index", 0)), fps=15)
            if not self.camera.start():
                return None
            if self.models.ready("eyes"):
                from jarvis.vision.faces import FaceEngine

                self.faces = FaceEngine(self.cfg.models_dir, self.cfg.faces_dir,
                                        owner_name=self.cfg.get("owner.name", "owner"))
                if self.faces.load():
                    self.camera.add_processor(self.faces.process)
        return self.camera

    def setup_speaker(self):
        """The speaker setup previews voices with. One voice model stays loaded (325 MB): switching
        voices re-points it instead of loading another copy, and when JARVIS is already running his
        own speaker is used, so the new voice applies to him straight away."""
        from jarvis.voice.tts import VOICE_PRESETS, Speaker, make_backend

        cfg = self.cfg
        choice = cfg.get("voice.tts.backend", "auto")
        want = "say" if choice == "say" or not self.models.ready("voice") else "kokoro-onnx"
        voice = cfg.get("voice.tts.voice", "jarvis")
        speed = float(cfg.get("voice.tts.speed", 1.0)) * VOICE_PRESETS.get(voice, {}).get("speed", 1.0)
        live = self.jarvis.speaker if self.jarvis is not None and getattr(self.jarvis, "speaker", None) else None
        for sp in (live, self.speaker):
            if sp is not None and sp.backend.name == want == "kokoro-onnx":
                sp.backend.set_voice(voice)
                sp.backend.speed = speed
                return sp
        key = (want, voice, cfg.get("voice.tts.say_voice"), speed)
        if self.speaker is None or getattr(self, "_speaker_key", None) != key:
            if self.speaker is not None:
                self.speaker.close()
            backend = make_backend(want, voice, float(cfg.get("voice.tts.speed", 1.0)),
                                   cfg.get("voice.tts.say_voice", "auto"), cfg.models_dir)
            self.speaker = Speaker(backend, cfg.get("voice.output_device"))
            self.speaker.start()
            self._speaker_key = key
        if live is not None and want == "say":
            live.backend = self.speaker.backend          # JARVIS switches to the Apple voice too
        return self.speaker

    def release_setup_devices(self) -> None:
        if self.camera is not None:
            try:
                self.camera.stop()
            except Exception:
                pass
        self.camera = None
        self.faces = None

    # ── window control (called from any thread) ───────────────────────────
    def setup_done(self) -> bool:
        return bool(self.cfg.setup_complete)

    def page_url(self, path: str) -> str:
        """A full address on JARVIS's own server. pywebview resolves a bare path like "/pair" against
        its *own* internal file server (a random port), which 404s, so never hand it a bare path."""
        if path.startswith(("http://", "https://")):
            return path
        port = int(self.cfg.get("hud.port", 7777))
        return f"http://127.0.0.1:{port}/{path.lstrip('/')}"

    def show_window(self, url: str | None = None) -> None:
        """Bring the window forward. Never blocks the caller: pywebview's window calls wait for the
        main thread (up to 20 s), so they run on a helper thread, never on JARVIS's event loop."""
        import threading

        threading.Thread(target=self._show_window, args=(url,), daemon=True, name="show-window").start()

    def _show_window(self, url: str | None = None) -> None:
        w = self.window
        if self.mini is not None:
            self.mini.hide()
        if w is None:
            return
        try:
            if url:
                w.load_url(self.page_url(url))
            w.show()
            w.restore()
        except Exception as exc:
            log.debug("show window: %s", exc)

    def status(self) -> dict[str, Any]:
        return {"running": self.jarvis is not None, "setup_complete": self.cfg.setup_complete}
