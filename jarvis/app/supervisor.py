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
        self.models = ModelManager(cfg.models_dir, cfg.get("voice.stt.model", "small.en"))
        self.camera = None          # setup-time camera (released before JARVIS starts)
        self.faces = None           # setup-time face engine
        self.speaker = None         # setup-time voice
        self.window = None          # pywebview window, set by the runner
        self.loop: asyncio.AbstractEventLoop | None = None
        self._downloads: asyncio.Task | None = None
        self._jarvis_task: asyncio.Task | None = None
        self.busy: dict[str, bool] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def boot(self) -> None:
        self.loop = asyncio.get_running_loop()
        bus.bind(self.loop)
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
        from jarvis.voice.tts import Speaker, make_backend

        choice = self.cfg.get("voice.tts.backend", "auto")
        want = "say" if choice == "say" or not self.models.ready("voice") else "kokoro-onnx"
        key = (want, self.cfg.get("voice.tts.voice"), self.cfg.get("voice.tts.say_voice"), self.cfg.get("voice.tts.speed"))
        if self.speaker is None or getattr(self, "_speaker_key", None) != key:
            if self.speaker is not None:
                self.speaker.stop()
            backend = make_backend(want, self.cfg.get("voice.tts.voice", "jarvis"),
                                   float(self.cfg.get("voice.tts.speed", 1.0)),
                                   self.cfg.get("voice.tts.say_voice", "auto"), self.cfg.models_dir)
            self.speaker = Speaker(backend, self.cfg.get("voice.output_device"))
            self.speaker.start()
            self._speaker_key = key
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
    def show_window(self, url: str | None = None) -> None:
        w = self.window
        if w is None:
            return
        try:
            if url:
                w.load_url(url)
            w.show()
            w.restore()
        except Exception as exc:
            log.debug("show window: %s", exc)

    def status(self) -> dict[str, Any]:
        return {"running": self.jarvis is not None, "setup_complete": self.cfg.setup_complete}
