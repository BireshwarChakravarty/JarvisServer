"""Hand *motion* gestures, on top of MediaPipe's static poses.

Pure geometry on the 21 hand landmarks (normalised image coordinates), so it is unit-testable
without a camera. Everything is expressed from the user's point of view. JARVIS's Camera already
mirrors frames like a selfie view (mirrored=True), so image-right is the user's right; pass
mirrored=False for a raw, un-mirrored camera.

The cursor follows the index fingertip through an "interaction box": the comfortable middle of
the camera view maps to the whole window, so you don't have to reach the frame's edges. A One Euro
filter (Casiez et al., CHI 2012) keeps it steady when your hand is still and lag-free when it moves.

  swipe left / right    a quick sideways sweep of the hand      → previous / next track
  swipe up              a quick upward flick                    → bring the HUD to the front
  pinch + drag up/down  thumb and index together, then move     → volume, like turning a dial
  pinch + drag sideways                                         → spin the hologram core
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

WRIST, THUMB_TIP, INDEX_TIP, MIDDLE_MCP = 0, 4, 8, 9


@dataclass
class MotionEvent:
    kind: str                    # swipe | pinch_start | pinch_drag | pinch_end
    direction: str = ""          # swipes: left/right/up/down; drags: x/y (the locked axis)
    dx: float = 0.0              # drag deltas since the previous frame, user's perspective
    dy: float = 0.0              # (+x = user's right, +y = up), in units of frame size
    total: float = 0.0           # drag: total travel along the locked axis since the pinch began


class OneEuro:
    """The 1€ filter: a low-pass whose cutoff rises with speed (jitter-free yet responsive)."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 4.0, d_cutoff: float = 1.0) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x: float | None = None
        self.dx = 0.0
        self.t: float | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, t: float, x: float) -> float:
        if self.x is None or self.t is None or t <= self.t:
            self.x, self.t = x, t
            return x
        dt = t - self.t
        self.t = t
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * (x - self.x) / dt + (1 - a_d) * self.dx
        a = self._alpha(self.min_cutoff + self.beta * abs(self.dx), dt)
        self.x = a * x + (1 - a) * self.x
        return self.x

    def reset(self) -> None:
        self.x, self.t, self.dx = None, None, 0.0


@dataclass
class HandState:
    visible: bool = False
    x: float = 0.5               # palm centre, user's perspective (0 = user's left, 1 = right)
    y: float = 0.5               # 0 = bottom, 1 = top
    pinch: bool = False
    px: float = 0.5              # the cursor: smoothed index fingertip mapped through the
    py: float = 0.5              # interaction box to 0..1 of the window (y: 0 = top)
    speed: float = 0.0           # palm speed, frame-widths per second
    events: list = field(default_factory=list)


class HandMotion:
    SWIPE_DIST = 0.20            # fraction of the frame
    SWIPE_TIME = 0.55            # seconds
    SWIPE_COOLDOWN = 0.8
    PINCH_ON, PINCH_OFF = 0.32, 0.50   # thumb-index gap relative to palm size (hysteresis)
    AXIS_LOCK = 0.035            # travel before a drag commits to an axis
    STILL_SPEED = 0.35           # below this the hand counts as held still

    def __init__(self, mirrored: bool = True, box: tuple = (0.15, 0.12, 0.85, 0.80)) -> None:
        self.mirrored = mirrored
        self.box = box                       # (left, bottom, right, top) in user coords, y up
        self.fx, self.fy = OneEuro(), OneEuro()
        self.trail: deque = deque()          # (t, x, y) palm centres
        self.pinched = False
        self._pinch_frames = 0
        self._origin: tuple[float, float] | None = None
        self._prev: tuple[float, float] | None = None
        self._axis: str | None = None
        self._cool_until = 0.0
        self.speed = 0.0

    def still(self) -> bool:
        """True when the hand is held steady (static poses like ✋ only count then)."""
        return self.speed < self.STILL_SPEED and not self.pinched

    def update(self, t: float, landmarks: list | None) -> HandState:
        if not landmarks:
            events = self._end_pinch()
            self.trail.clear()
            self.speed = 0.0
            self.fx.reset(); self.fy.reset()
            return HandState(visible=False, events=events)
        pts = [((p[0] if self.mirrored else 1.0 - p[0]), 1.0 - p[1]) for p in landmarks]   # user's view; y up
        cx = (pts[WRIST][0] + pts[MIDDLE_MCP][0]) / 2
        cy = (pts[WRIST][1] + pts[MIDDLE_MCP][1]) / 2
        size = max(1e-3, math.dist(pts[WRIST], pts[MIDDLE_MCP]))
        gap = math.dist(pts[THUMB_TIP], pts[INDEX_TIP]) / size

        self.trail.append((t, cx, cy))
        while self.trail and t - self.trail[0][0] > self.SWIPE_TIME:
            self.trail.popleft()
        if len(self.trail) >= 2:
            (t0, x0, y0), (t1, x1, y1) = self.trail[-2], self.trail[-1]
            inst = math.dist((x0, y0), (x1, y1)) / max(1e-3, t1 - t0)
            self.speed = 0.6 * self.speed + 0.4 * inst
        events: list[MotionEvent] = []

        # pinch, with hysteresis and a two-frame debounce so a passing thumb doesn't count
        if not self.pinched:
            self._pinch_frames = self._pinch_frames + 1 if gap < self.PINCH_ON else 0
            if self._pinch_frames >= 2:
                self.pinched, self._origin, self._prev, self._axis = True, (cx, cy), (cx, cy), None
                self.trail.clear()
                events.append(MotionEvent("pinch_start"))
        elif gap > self.PINCH_OFF:
            events += self._end_pinch()
        else:
            ox, oy = self._origin
            if self._axis is None and max(abs(cx - ox), abs(cy - oy)) > self.AXIS_LOCK:
                self._axis = "x" if abs(cx - ox) > abs(cy - oy) else "y"
            if self._axis:
                px, py = self._prev
                dx, dy = cx - px, cy - py
                total = (cx - ox) if self._axis == "x" else (cy - oy)
                events.append(MotionEvent("pinch_drag", self._axis,
                                          dx if self._axis == "x" else 0.0, dy if self._axis == "y" else 0.0, total))
            self._prev = (cx, cy)

        # swipes: an open hand travelling far, fast and mostly along one axis
        if not self.pinched and t >= self._cool_until and len(self.trail) >= 3:
            ts, xs, ys = self.trail[0]
            dx, dy = cx - xs, cy - ys
            if t - ts <= self.SWIPE_TIME:
                if abs(dx) >= self.SWIPE_DIST and abs(dy) < 0.55 * abs(dx):
                    events.append(MotionEvent("swipe", "right" if dx > 0 else "left"))
                elif abs(dy) >= self.SWIPE_DIST * 1.1 and abs(dx) < 0.55 * abs(dy):
                    events.append(MotionEvent("swipe", "up" if dy > 0 else "down"))
                if events and events[-1].kind == "swipe":
                    self._cool_until = t + self.SWIPE_COOLDOWN
                    self.trail.clear()
        if any(e.kind == "pinch_end" for e in events):
            self._cool_until = t + 0.6   # letting go is not a swipe
            self.trail.clear()
        px, py = self._cursor(t, pts)
        return HandState(True, cx, cy, self.pinched, px, py, self.speed, events)

    def _cursor(self, t: float, pts: list) -> tuple[float, float]:
        l, b, r, top = self.box
        # while pinching, the fingertip dives to the thumb: aim with the pinch point instead
        tip = pts[INDEX_TIP] if not self.pinched else (
            (pts[INDEX_TIP][0] + pts[THUMB_TIP][0]) / 2, (pts[INDEX_TIP][1] + pts[THUMB_TIP][1]) / 2)
        x = min(1.0, max(0.0, (tip[0] - l) / (r - l)))
        y = min(1.0, max(0.0, (top - tip[1]) / (top - b)))      # screen y grows downwards
        return self.fx(t, x), self.fy(t, y)

    def _end_pinch(self) -> list[MotionEvent]:
        if not self.pinched:
            self._pinch_frames = 0
            return []
        total = 0.0
        if self._origin and self._prev and self._axis:
            total = (self._prev[0] - self._origin[0]) if self._axis == "x" else (self._prev[1] - self._origin[1])
        self.pinched, self._pinch_frames, self._origin, self._prev = False, 0, None, None
        axis, self._axis = self._axis or "", None
        return [MotionEvent("pinch_end", axis, total=total)]


def attention(landmarks: list | tuple, box_w: float) -> float:
    """How squarely a face is turned to the camera, 0 (profile) to 1 (straight on).

    YuNet landmarks: right eye, left eye, nose tip, mouth corners. The nose sits midway between
    the eyes when you face the screen and swings towards one eye as you turn away.
    """
    (rex, rey), (lex, ley), (nx, _ny) = landmarks[0], landmarks[1], landmarks[2]
    eye_dist = math.dist((rex, rey), (lex, ley))
    if eye_dist < 0.15 * box_w:          # eyes nearly on top of each other: head turned right away
        return 0.0
    yaw = abs(nx - (rex + lex) / 2) / eye_dist   # 0 straight on, ~0.5+ well turned
    return max(0.0, min(1.0, 1.0 - yaw / 0.45))
