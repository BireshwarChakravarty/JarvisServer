"""Camera capture on a background thread. Shares the latest frame with face
recognition, gestures, the HUD (MJPEG stream) and Claude (on request)."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

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
        self._worker: threading.Thread | None = None
        self._seq = 0
        self._new = threading.Event()
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
        self._worker = threading.Thread(target=self._process_loop, daemon=True, name="camera-processing")
        self._worker.start()
        log.info("camera %s started", self.index)
        return True

    def stop(self) -> None:
        self._running = False
        self._new.set()
        for t in (self._thread, self._worker):
            if t:
                t.join(timeout=2)
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
                self._seq += 1
            self._new.set()
            time.sleep(max(0.0, interval - (time.monotonic() - t0)))

    def _process_loop(self) -> None:
        """Recognition runs beside capture, always on the newest frame. If it falls behind it skips
        frames instead of queueing them, so the hand cursor never trails behind your hand."""
        done = 0
        while self._running:
            if not self._new.wait(0.5):
                continue
            self._new.clear()
            with self._lock:
                frame, seq = self._frame, self._seq
            if frame is None or seq == done:
                continue
            done = seq
            for fn in list(self._processors):
                try:
                    fn(frame)
                except Exception:  # never let a processor kill recognition
                    log.exception("frame processor failed")

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
            frame = cv2.resize(frame, (max_width, int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        frame = brighten_low_light(frame)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None


_LUTS: dict = {}


def brighten_low_light(frame: np.ndarray) -> np.ndarray:
    """Preview only (recognition sees the raw frame): lift a dim room's midtones with a gamma curve
    so faces read clearly, keeping colour. A well-lit frame passes through untouched."""
    small = frame[::16, ::16]
    luma = float((0.114 * small[..., 0] + 0.587 * small[..., 1] + 0.299 * small[..., 2]).mean())
    if luma >= 100:
        return frame
    gamma = max(0.55, min(1.0, np.log(0.42) / np.log(max(luma, 8) / 255)))
    key = round(gamma, 2)
    lut = _LUTS.get(key)
    if lut is None:
        lut = _LUTS[key] = (((np.arange(256) / 255.0) ** key) * 255).clip(0, 255).astype(np.uint8)
    return lut[frame]
