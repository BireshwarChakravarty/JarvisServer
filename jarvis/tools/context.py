"""Everything a tool might need, passed as the first argument to every tool."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolContext:
    cfg: Any
    memory: Any = None
    camera: Any = None           # jarvis.vision.camera.Camera
    faces: Any = None            # jarvis.vision.faces.FaceEngine
    presence: Any = None         # jarvis.vision.presence.Presence
    scheduler: Any = None        # jarvis.core.scheduler.Scheduler
    protocols: Any = None        # jarvis.core.protocols.Protocols
    orchestrator: Any = None     # back-reference for speak/listen
    confirm: Callable[[str], Awaitable[bool]] | None = None
    require_confirmation: bool = True
    extras: dict = field(default_factory=dict)

    async def say(self, text: str) -> None:
        if self.orchestrator is not None:
            await self.orchestrator.say(text)
