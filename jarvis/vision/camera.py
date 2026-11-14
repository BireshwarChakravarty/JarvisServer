"""Camera capture on a background thread. Shares the latest frame with face
recognition, gestures, the HUD (MJPEG stream) and Claude (on request)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

log = logging.getLogger("jarvis.camera")


class Camera:
    def __init__(self, index: int = 0, fps: int = 15, width: int = 1280, height: int = 720) -> None:
        self.index = index
        self.fps = fps
        self.width, self.height = width, height
        self._cap = None
        self._frame: np.ndarray | None = None
        self._frame_ts = 0.0
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._processors: list[Callable[[np.ndarray], None]] = []
        self.available = False
        self.error = ""

    def add_processor(self, fn: Callable[[np.ndarray], None]) -> None:
        """fn(frame) is called on the capture thread for every frame (keep it quick)."""
        self._processors.append(fn)

    def start(self) -> bool:
        try:
            import cv2
        except ImportError:
            self.error = "opencv not installed (pip install 'jarvis-mac[vision]')"
            log.warning(self.error)
            return False
        cap = cv2.VideoCapture(self.index, cv2.CAP_AVFOUNDATION) if hasattr(cv2, "CAP_AVFOUNDATION") \
            else cv2.VideoCapture(self.index)
        if not cap or not cap.isOpened():
            cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            self.error = "camera not available (check System Settings › Privacy & Security › Camera)"
            log.warning(self.error)
            return False
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._cap = cap
        self._running = True
        self.available = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="camera")
        self._thread.start()
        log.info("camera %s started", self.index)
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap is not None:
            self._cap.release()
        self.available = False

    def _loop(self) -> None:
        interval = 1.0 / max(1, self.fps)
        failures = 0
        while self._running:
            t0 = time.monotonic()
            ok, frame = self._cap.read()
            if not ok or frame is None:
                failures += 1
                if failures > 50:
                    self.error = "camera stopped delivering frames"
                    log.error(self.error)
                    self.available = False
                    break
                time.sleep(0.1)
                continue
            failures = 0
            frame = frame[:, ::-1].copy()  # mirror, like a selfie view
            with self._lock:
                self._frame = frame
                self._frame_ts = time.time()
            for fn in self._processors:
                try:
                    fn(frame)
                except Exception:  # never let a processor kill capture
                    log.exception("frame processor failed")
            time.sleep(max(0.0, interval - (time.monotonic() - t0)))

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def jpeg(self, quality: int = 80, max_width: int | None = None) -> bytes | None:
        import cv2

        frame = self.latest()
        if frame is None:
            return None
        if max_width and frame.shape[1] > max_width:
            scale = max_width / frame.shape[1]
            frame = cv2.resize(frame, (max_width, int(frame.shape[0] * scale)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None
