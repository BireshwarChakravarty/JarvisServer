"""Hand gestures via MediaPipe Gesture Recognizer.

  ✋ Open palm (hold)   → stop talking / pause
  👍 Thumb up           → "yes" (confirms a pending action)
  👎 Thumb down         → "no"
  ✌️ Victory            → start listening (no wake word needed)
  🤟 I-love-you         → play / pause music
"""
from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np

from jarvis.events import bus

log = logging.getLogger("jarvis.gestures")

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
             "gesture_recognizer/float16/latest/gesture_recognizer.task")

ACTIONS = {
    "Open_Palm": "stop",
    "Thumb_Up": "yes",
    "Thumb_Down": "no",
    "Victory": "listen",
    "ILoveYou": "media_toggle",
}


class GestureEngine:
    def __init__(self, models_dir: Path, on_gesture: Callable[[str, str], None],
                 fps: float = 8.0, hold_frames: int = 4, cooldown: float = 2.0) -> None:
        self.models_dir = models_dir
        self.on_gesture = on_gesture
        self.interval = 1.0 / fps
        self.hold_frames = hold_frames
        self.cooldown = cooldown
        self._recognizer = None
        self._mp = None
        self._last = 0.0
        self._history: deque = deque(maxlen=hold_frames)
        self._cool_until = 0.0
        self._t0 = time.monotonic()
        self.available = False

    def load(self) -> bool:
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            from jarvis.voice.tts import _download

            path = self.models_dir / "gesture_recognizer.task"
            if not path.exists():
                log.info("downloading gesture model …")
                _download(MODEL_URL, path)
            options = vision.GestureRecognizerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(path)),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=0.6,
                min_tracking_confidence=0.5,
            )
            self._recognizer = vision.GestureRecognizer.create_from_options(options)
            self._mp = mp
            self.available = True
            log.info("gesture recognition ready")
        except Exception as exc:
            log.warning("gestures unavailable: %s", exc)
            self.available = False
        return self.available

    def process(self, frame: np.ndarray) -> None:
        now = time.monotonic()
        if not self.available or now - self._last < self.interval:
            return
        self._last = now
        rgb = frame[:, :, ::-1].copy()
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._recognizer.recognize_for_video(image, int((now - self._t0) * 1000))
        label = "None"
        if result.gestures and result.gestures[0]:
            top = result.gestures[0][0]
            if top.score >= 0.6:
                label = top.category_name
        self._history.append(label)
        if (label in ACTIONS and len(self._history) == self.hold_frames
                and all(h == label for h in self._history) and now >= self._cool_until):
            self._cool_until = now + self.cooldown
            self._history.clear()
            action = ACTIONS[label]
            bus.emit("gesture", gesture=label, action=action)
            log.info("gesture %s → %s", label, action)
            self.on_gesture(label, action)
