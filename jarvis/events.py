"""A tiny thread-safe async event bus. Every subsystem publishes here; the HUD
(and anything else) subscribes. Events are plain JSON-able dicts."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

log = logging.getLogger("jarvis.events")


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.history: deque[dict] = deque(maxlen=history)
        self.state: dict[str, Any] = {"state": "booting"}

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def emit(self, type_: str, **payload: Any) -> None:
        """Publish from any thread."""
        event = {"type": type_, "ts": time.time(), **payload}
        if type_ not in ("level", "stats", "faces"):
            self.history.append(event)
        if type_ == "state":
            self.state["state"] = payload.get("state")
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._fanout(event)
        else:
            loop.call_soon_threadsafe(self._fanout, event)

    def _fanout(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # slow consumer: drop the oldest to keep the HUD live
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:  # pragma: no cover
                    pass


bus = EventBus()
