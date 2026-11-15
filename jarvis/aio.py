"""spawn(): start background work safely.

asyncio only keeps weak references to tasks, so a task nobody holds can be garbage-collected
mid-run and vanish silently; and an exception in one is never seen. spawn() keeps a reference
until the task ends and logs any failure.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("jarvis.tasks")
_live: set[asyncio.Future] = set()


def spawn(coro, name: str | None = None) -> asyncio.Future:
    task = asyncio.ensure_future(coro)
    if name and hasattr(task, "set_name"):
        task.set_name(name)
    _live.add(task)
    task.add_done_callback(_finished)
    return task


def _finished(task: asyncio.Future) -> None:
    _live.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("background task %s failed", getattr(task, "get_name", lambda: "?")(), exc_info=exc)
