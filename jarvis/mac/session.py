"""Watches the macOS login session: screen locked/unlocked and system sleep/wake.

Uses Quartz's CGSessionCopyCurrentDictionary (cheap, reliable, no run loop needed).
A gap in the poll timer reveals that the machine slept."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from jarvis.mac.system import IS_MAC

log = logging.getLogger("jarvis.session")


def screen_locked() -> bool | None:
    if not IS_MAC:
        return None
    try:
        import Quartz

        d = Quartz.CGSessionCopyCurrentDictionary()
        if d is None:
            return True  # no GUI session (login window)
        return bool(d.get("CGSSessionScreenIsLocked", False)) or not bool(d.get("kCGSSessionOnConsoleKey", True))
    except Exception:
        return None


def idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input."""
    if not IS_MAC:
        return None
    try:
        import Quartz

        return float(Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType))
    except Exception:
        return None


class SessionWatcher:
    def __init__(self, on_unlock: Callable[[float], Awaitable[None]],
                 on_lock: Callable[[], Awaitable[None]] | None = None,
                 on_wake: Callable[[float], Awaitable[None]] | None = None,
                 interval: float = 1.5) -> None:
        self.on_unlock = on_unlock
        self.on_lock = on_lock
        self.on_wake = on_wake
        self.interval = interval
        self.locked = screen_locked()
        self.locked_at = time.time() if self.locked else 0.0

    async def run(self) -> None:
        if not IS_MAC:
            log.info("session watcher disabled (not macOS)")
            return
        last_tick = time.time()
        while True:
            await asyncio.sleep(self.interval)
            now = time.time()
            gap = now - last_tick
            last_tick = now
            if gap > 30 and self.on_wake:
                log.info("system woke after %.0fs", gap)
                await self.on_wake(gap)
            state = screen_locked()
            if state is None or state == self.locked:
                continue
            self.locked = state
            if state:
                self.locked_at = now
                log.info("screen locked")
                if self.on_lock:
                    await self.on_lock()
            else:
                away = now - (self.locked_at or now)
                log.info("screen unlocked after %.0fs", away)
                await self.on_unlock(away)
