"""Presence: turns per-frame face sightings into "arrived" / "left" events,
with hysteresis so a glance away or a blink doesn't count. Also sentry mode."""
from __future__ import annotations

import logging
import time
from typing import Callable

from jarvis.events import bus

log = logging.getLogger("jarvis.presence")


class Presence:
    def __init__(self, owner: str, away_after: float = 600.0, gone_after: float = 25.0,
                 on_arrive: Callable[[float], None] | None = None,
                 on_leave: Callable[[], None] | None = None,
                 on_stranger: Callable[[], None] | None = None) -> None:
        self.owner = owner
        self.away_after = away_after      # seconds absent before a return counts as "arrival"
        self.gone_after = gone_after      # seconds with no face before we decide you left
        self.on_arrive = on_arrive
        self.on_leave = on_leave
        self.on_stranger = on_stranger
        self.present = False
        self.last_seen = 0.0
        self.left_at = time.time()
        self.owner_seen = 0.0
        self.stranger_seen_at = 0.0
        self._stranger_streak = 0
        self._owner_streak = 0
        self.recognition = True  # False when no face is enrolled: any face counts as the owner

    def update(self, faces: list, now: float | None = None) -> None:
        """faces: list of objects with .name (None = unknown)."""
        now = now or time.time()
        anyone = bool(faces)
        owner_here = any(f.name == self.owner for f in faces) or (anyone and not self.recognition)
        unknown_only = anyone and not owner_here and all(f.name is None for f in faces)

        if owner_here:
            self._owner_streak += 1
            self.owner_seen = now
        else:
            self._owner_streak = 0

        if anyone:
            self.last_seen = now
            # the owner (2 consecutive sightings) or another *known* person counts as arrival
            if not self.present and ((owner_here and self._owner_streak >= 2) or
                                     (not owner_here and not unknown_only)):
                self._arrive(now)
        elif self.present and now - self.last_seen > self.gone_after:
            self.present = False
            self.left_at = self.last_seen
            bus.emit("presence", present=False)
            log.info("user left")
            if self.on_leave:
                self.on_leave()

        # sentry: an unknown face for several consecutive updates while the owner is away
        if unknown_only and not self.present:
            self._stranger_streak += 1
            if self._stranger_streak == 6 and now - self.stranger_seen_at > 120:
                self.stranger_seen_at = now
                bus.emit("presence", stranger=True)
                if self.on_stranger:
                    self.on_stranger()
        else:
            self._stranger_streak = 0

    def _arrive(self, now: float) -> None:
        away_for = now - self.left_at
        self.present = True
        bus.emit("presence", present=True, away_for=int(away_for))
        log.info("user arrived after %.0fs away", away_for)
        if away_for >= self.away_after and self.on_arrive:
            self.on_arrive(away_for)

    @property
    def status(self) -> dict:
        return {
            "present": self.present,
            "owner_last_seen_s_ago": int(time.time() - self.owner_seen) if self.owner_seen else None,
        }
