"""Camera: recognition runs off the capture thread on the newest frame only; preview keeps colour."""
import threading
import time

import numpy as np


class FakeCap:
    def __init__(self):
        self.n = 0

    def read(self):
        self.n += 1
        f = np.zeros((72, 128, 3), np.uint8)
        f[0, 0, 0] = self.n % 256
        return True, f

    def release(self):
        pass


def test_slow_processors_skip_to_the_newest_frame():
    from jarvis.vision.camera import Camera

    cam = Camera(fps=200)
    cam._cap, cam._running, cam.available = FakeCap(), True, True
    seen = []

    def slow(frame):
        seen.append(int(frame[0, -1, 0]))   # mirrored: pixel (0,0) moved to the last column
        time.sleep(0.05)

    cam.add_processor(slow)
    cam._thread = threading.Thread(target=cam._loop, daemon=True)
    cam._worker = threading.Thread(target=cam._process_loop, daemon=True)
    cam._thread.start(); cam._worker.start()
    time.sleep(0.6)
    captured = cam._cap.n
    cam.stop()
    assert captured > 3 * len(seen)            # capture never waited for recognition
    assert len(seen) >= 3 and all(b > a for a, b in zip(seen, seen[1:]))   # always fresher frames


def test_preview_brightening_keeps_colour():
    from jarvis.vision.camera import brighten_low_light

    dim = np.zeros((64, 64, 3), np.uint8)
    dim[..., 2] = 60; dim[..., 1] = 30; dim[..., 0] = 10    # a dim, warm (BGR) room
    out = brighten_low_light(dim)
    assert out.mean() > dim.mean() * 1.5 and out[..., 2].mean() > out[..., 1].mean() > out[..., 0].mean()
