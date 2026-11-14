"""Protocols: named, user-defined routines ("work mode", "house party protocol").

Defined in ~/.jarvis/protocols.yaml. Each step maps onto a JARVIS tool, so anything
JARVIS can do, a protocol can do — including running macOS Shortcuts (HomeKit scenes,
Focus modes …) and handing a natural-language request back to the brain."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("jarvis.protocols")

DEFAULT_PROTOCOLS = """\
# ─────────────────────────────────────────────────────────────────────────
#  JARVIS protocols. Say "Jarvis, engage <name>" or any alias.
#  Step types:
#    open_app: <name>          quit_app: <name>        open_url: <url>
#    shortcut: <Shortcuts name> (HomeKit scenes, Focus modes, anything)
#    volume: 0-100             brightness: 0-100       dark_mode: true|false
#    music: <song/playlist>    media: play|pause|next|previous
#    say: <text>               wait: <seconds>         notify: <text>
#    applescript: <script>     shell: <command>        (confirmed before running)
#    ask: <request>            hand a request to JARVIS's brain after the protocol
# ─────────────────────────────────────────────────────────────────────────

work_mode:
  aliases: ["work mode", "focus mode", "let's get to work"]
  description: "Clear the decks for deep work."
  say: "Work mode engaged. I'll keep distractions at bay."
  steps:
    - volume: 25
    - open_app: "Calendar"
    - music: "Deep Focus"
    - ask: "Give me a ten second rundown of my next three calendar events."

house_party_protocol:
  aliases: ["house party protocol", "party mode"]
  description: "Lights, music, volume. Party."
  say: "House party protocol, initiated."
  steps:
    - dark_mode: true
    - volume: 70
    - music: "Party"
    # - shortcut: "Party Lights"   # a HomeKit scene in the Shortcuts app

wind_down:
  aliases: ["wind down", "end of day", "clean slate protocol"]
  description: "Close out the day."
  say: "Winding down. Well done today."
  steps:
    - media: pause
    - ask: "Summarise what's on my calendar tomorrow morning and any reminders I haven't finished."

good_night:
  aliases: ["good night", "goodnight", "lights out"]
  description: "Lock up for the night."
  say: "Good night. I'll keep an eye on things."
  steps:
    - media: pause
    - wait: 2
    - lock_screen: true
"""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower().replace("_", " ")).strip()


class Protocols:
    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.exists():
            path.write_text(DEFAULT_PROTOCOLS)
        self.data: dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        try:
            self.data = yaml.safe_load(self.path.read_text()) or {}
        except Exception as exc:
            log.error("couldn't parse %s: %s", self.path, exc)
            self.data = {}

    def list(self) -> list[dict]:
        return [{"name": k, "description": v.get("description", ""), "aliases": v.get("aliases", [])}
                for k, v in self.data.items()]

    def _names(self, key: str, proto: dict) -> set[str]:
        names = {_norm(key)} | {_norm(a) for a in proto.get("aliases", []) or []}
        return names | {re.sub(r"\s+protocol$", "", n) for n in names}

    def find(self, name: str) -> tuple[str, dict] | None:
        want = re.sub(r"^(engage|initiate|activate|run|start)\s+(the\s+)?", "", _norm(name))
        variants = {want, re.sub(r"\s+protocol$", "", want)}
        for key, proto in self.data.items():
            if variants & self._names(key, proto):
                return key, proto
        for key, proto in self.data.items():  # loose contains-match
            if any(want and (want in n or n in want) for n in self._names(key, proto)):
                return key, proto
        return None

    async def run(self, name: str, registry, ctx) -> str:
        found = self.find(name)
        if not found:
            options = ", ".join(self.data) or "none defined"
            return f"No protocol called {name!r}. Available: {options}."
        key, proto = found
        from jarvis.events import bus

        bus.emit("protocol", name=key, status="start")
        log.info("running protocol %s", key)
        if proto.get("say") and ctx.orchestrator is not None:
            await ctx.say(proto["say"])
        report: list[str] = []
        followups: list[str] = []
        for step in proto.get("steps", []) or []:
            if not isinstance(step, dict) or len(step) != 1:
                report.append(f"skipped malformed step {step!r}")
                continue
            (kind, value), = step.items()
            try:
                outcome = await self._step(kind, value, registry, ctx, followups)
                report.append(f"{kind}: {outcome}")
            except Exception as exc:
                report.append(f"{kind}: failed ({exc})")
        if followups and ctx.orchestrator is not None:
            for req in followups:
                ctx.orchestrator.queue_request(req)
        bus.emit("protocol", name=key, status="done")
        prefix = "Already announced to the user. " if proto.get("say") else ""
        return prefix + f"Protocol {key} complete. " + "; ".join(report)

    async def _step(self, kind: str, value: Any, registry, ctx, followups: list[str]) -> str:
        mapping = {
            "open_app": ("open_application", lambda v: {"name": v}),
            "quit_app": ("quit_application", lambda v: {"name": v}),
            "open_url": ("open_url", lambda v: {"url": v}),
            "shortcut": ("run_shortcut", lambda v: {"name": v}),
            "volume": ("set_volume", lambda v: {"level": int(v)}),
            "brightness": ("set_brightness", lambda v: {"level": int(v)}),
            "dark_mode": ("system_action", lambda v: {"action": "dark_mode_on" if v else "dark_mode_off"}),
            "music": ("play_music", lambda v: {"query": str(v)}),
            "media": ("media_control", lambda v: {"action": str(v)}),
            "notify": ("show_notification", lambda v: {"title": "JARVIS", "message": str(v)}),
            "applescript": ("run_applescript", lambda v: {"script": str(v)}),
            "shell": ("run_shell", lambda v: {"command": str(v)}),
            "lock_screen": ("system_action", lambda v: {"action": "lock_screen"}),
        }
        if kind == "wait":
            await asyncio.sleep(float(value))
            return "ok"
        if kind == "say":
            await ctx.say(str(value))
            return "said"
        if kind == "ask":
            followups.append(str(value))
            return "queued"
        if kind not in mapping:
            return f"unknown step type {kind!r}"
        tool, build = mapping[kind]
        result = await registry.call(tool, build(value), ctx)
        return result.summary
