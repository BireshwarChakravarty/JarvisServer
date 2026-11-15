"""Hand gestures via MediaPipe Gesture Recognizer.

  ✋ Open palm (hold)   → stop talking / pause
  👍 Thumb up           → "yes" (confirms a pending action)
  👎 Thumb down         → "no"
  ✌️ Victory            → start listening (no wake word needed)
  🤟 I-love-you         → play / pause music

Motion gestures (see motion.py): swipe ← → for tracks, flick ↑ to bring up the HUD,
pinch and drag up/down for volume, pinch and drag sideways to spin the hologram.
Static poses only count while the hand is held still, so a swipe never reads as ✋.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np

from jarvis.events import bus
from jarvis.vision.motion import HandMotion, MotionEvent

log = logging.getLogger("jarvis.gestures")

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
             "gesture_recognizer/float16/latest/gesture_recognizer.task")

SWIPES = {"left": "previous", "right": "next", "up": "show_hud"}

ACTIONS = {
    "Open_Palm": "stop",
    "Thumb_Up": "yes",
    "Thumb_Down": "no",
    "Victory": "listen",
    "ILoveYou": "media_toggle",
}


class GestureEngine:
    def __init__(self, models_dir: Path, on_gesture: Callable[[str, str], None],
                 fps: float = 10.0, hold_frames: int = 5, cooldown: float = 2.0,
                 on_motion: Callable[[MotionEvent], None] | None = None,
                 tracking_fps: float = 24.0, air_mouse: bool = False) -> None:
        self.models_dir = models_dir
        self.on_gesture = on_gesture
        self.on_motion = on_motion
        self.motion = HandMotion()
        self._was_visible = False
        self._seen = -10.0
        self.tracking_interval = 1.0 / tracking_fps   # a hand in view: run fast so the cursor keeps up
        self.airmouse = None
        if air_mouse:
            from jarvis.vision.airmouse import AirMouse

            def changed(on: bool) -> None:
                bus.emit("airmouse", active=on)
                from jarvis.app.mini import click_through

                click_through(on)   # the floating capsule must never catch a hand-made click

            self.airmouse = AirMouse(on_change=changed)
        self.interval = 1.0 / fps                      # nobody there: idle along
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
        interval = self.tracking_interval if now - self._seen < 1.5 else self.interval
        if not self.available or now - self._last < interval:
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
        lms = [(p.x, p.y) for p in result.hand_landmarks[0]] if getattr(result, "hand_landmarks", None) else None
        if lms:
            self._seen = now
        if self.handle_motion(now, lms, label):
            self._history.clear()
            return                    # the air mouse owns the hand: no other gestures
        if not self.motion.still():
            label = "Moving"          # a hand in motion is never a held pose
        self._history.append(label)
        if (label in ACTIONS and len(self._history) == self.hold_frames
                and all(h == label for h in self._history) and now >= self._cool_until):
            self._cool_until = now + self.cooldown
            self._history.clear()
            action = ACTIONS[label]
            bus.emit("gesture", gesture=label, action=action)
            log.info("gesture %s → %s", label, action)
            self.on_gesture(label, action)

    def handle_motion(self, now: float, lms: list | None, label: str = "None") -> bool:
        """Feed one frame's landmarks to the motion tracker; publish the cursor and motion events.
        Returns True when the air mouse has taken over the hand."""
        st = self.motion.update(now, lms)
        if st.visible or self._was_visible:
            # x/y: the cursor in window fractions (y down), already mirrored and smoothed
            bus.emit("hand", visible=st.visible, x=round(st.px, 4), y=round(st.py, 4), pinch=st.pinch)
        self._was_visible = st.visible
        if self.airmouse is not None and self.airmouse.update(now, label, st):
            return True
        for ev in st.events:
            if ev.kind == "swipe":
                action = SWIPES.get(ev.direction)
                bus.emit("gesture", gesture=f"Swipe_{ev.direction}", action=action or "none")
                if action:
                    log.info("swipe %s → %s", ev.direction, action)
                    self.on_gesture(f"Swipe_{ev.direction}", action)
            elif self.on_motion:
                self.on_motion(ev)
        return False
