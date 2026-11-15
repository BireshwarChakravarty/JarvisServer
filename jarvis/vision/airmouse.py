"""Air mouse: drive the real macOS pointer with your hand (opt-in: vision.air_mouse: true).

  ☝️ hold ½ s        start: the pointer follows your index fingertip
  pinch              click (pinch and move = drag)
  ✋ hold ½ s         stop  (or take your hand away for two seconds)

While it is on, swipes and the pinch volume dial are paused so nothing fights over your hand.
The mode logic is pure and testable; MacPointer posts the actual Quartz mouse events, which needs
the Accessibility permission JARVIS already asks for during setup.
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.airmouse")


class MacPointer:
    """Posts mouse events with Quartz. Coordinates are 0..1 of the main display."""

    def __init__(self) -> None:
        import Quartz

        self.Q = Quartz
        b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        self.w, self.h = b.size.width, b.size.height
        self.down_ = False

    def _post(self, kind, x: float, y: float) -> None:
        Q = self.Q
        pt = (x * self.w, y * self.h)
        ev = Q.CGEventCreateMouseEvent(None, kind, pt, Q.kCGMouseButtonLeft)
        Q.CGEventPost(Q.kCGHIDEventTap, ev)

    def move(self, x: float, y: float) -> None:
        Q = self.Q
        self._post(Q.kCGEventLeftMouseDragged if self.down_ else Q.kCGEventMouseMoved, x, y)

    def down(self, x: float, y: float) -> None:
        self.down_ = True
        self._post(self.Q.kCGEventLeftMouseDown, x, y)

    def up(self, x: float, y: float) -> None:
        self.down_ = False
        self._post(self.Q.kCGEventLeftMouseUp, x, y)


class AirMouse:
    HOLD = 0.5          # seconds a pose must be held to start/stop
    LOST = 2.0          # hand gone this long: stop

    def __init__(self, pointer=None, on_change=None) -> None:
        self.pointer = pointer
        self.on_change = on_change or (lambda active: None)
        self.active = False
        self._pose: str | None = None
        self._pose_since = 0.0
        self._last_seen = 0.0
        self._down = False
        self._pos = (0.5, 0.5)

    def _ptr(self):
        if self.pointer is None:
            self.pointer = MacPointer()
        return self.pointer

    def _set(self, active: bool) -> None:
        if active == self.active:
            return
        if not active and self._down:
            self._ptr().up(*self._pos)
            self._down = False
        self.active = active
        log.info("air mouse %s", "on" if active else "off")
        self.on_change(active)

    def update(self, t: float, label: str, state) -> bool:
        """Feed one frame (pose label + HandState). Returns True while the air mouse owns the hand."""
        if state.visible:
            self._last_seen = t
        elif self.active and t - self._last_seen > self.LOST:
            self._set(False)
            return False
        if label != self._pose:
            self._pose, self._pose_since = label, t
        held = t - self._pose_since >= self.HOLD
        if not self.active:
            if label == "Pointing_Up" and held:
                self._set(True)
                self._pose_since = t   # don't immediately reuse the hold
            return self.active
        if label == "Open_Palm" and held and not state.pinch:
            self._set(False)
            return False
        if not state.visible:
            return True
        self._pos = (state.px, state.py)
        p = self._ptr()
        if state.pinch and not self._down:
            p.down(*self._pos)
            self._down = True
        elif not state.pinch and self._down:
            p.up(*self._pos)
            self._down = False
        else:
            p.move(*self._pos)
        return True
