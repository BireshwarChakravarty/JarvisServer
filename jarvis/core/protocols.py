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
#    timer: <minutes>          or {minutes: 25, label: "Focus sprint"}
#    hand_control: true|false  gestures and air mouse on/off
#    quit_apps: [names]        hide_others: true         sleep_display: true
#  Your own protocols go in this file; built-in ones are always available too
#  (yours win on a name clash; add `disabled: true` to hide a built-in).
#  `pinned: true` puts a protocol on the HUD.
# ─────────────────────────────────────────────────────────────────────────

work_mode:
  pinned: true
  aliases: ["work mode", "focus mode", "let's get to work"]
  description: "Clear the decks for deep work."
  say: "Work mode engaged. I'll keep distractions at bay."
  steps:
    - volume: 25
    - open_app: "Calendar"
    - music: "Deep Focus"
    - ask: "Give me a ten second rundown of my next three calendar events."

house_party_protocol:
  pinned: true
  aliases: ["house party protocol", "party mode"]
  description: "Lights, music, volume. Party."
  say: "House party protocol, initiated."
  steps:
    - dark_mode: true
    - volume: 70
    - music: "Party"
    # - shortcut: "Party Lights"   # a HomeKit scene in the Shortcuts app

wind_down:
  pinned: true
  aliases: ["wind down", "end of day", "clean slate protocol"]
  description: "Close out the day."
  say: "Winding down. Well done today."
  steps:
    - media: pause
    - ask: "Summarise what's on my calendar tomorrow morning and any reminders I haven't finished."

good_night:
  pinned: true
  aliases: ["good night", "goodnight", "lights out"]
  description: "Lock up for the night."
  say: "Good night. I'll keep an eye on things."
  steps:
    - media: pause
    - wait: 2
    - lock_screen: true

morning_protocol:
  aliases: ["good morning", "morning protocol", "morning briefing", "start my day"]
  description: "Ease into the day with a proper briefing."
  say: "Good morning. Let's see what the day has in store."
  steps:
    - volume: 35
    - ask: "Give me my morning briefing: the weather and whether I need a jacket, my first three events, how many unread emails, and any reminders due today."

meeting_mode:
  aliases: ["meeting mode", "i'm going into a meeting", "going into a meeting"]
  description: "Quiet the Mac and brief you on the next meeting."
  say: "Meeting mode. I'll keep quiet."
  steps:
    - media: pause
    - volume: 15
    - ask: "What's my next meeting, when does it start, who's in it, and is there a video link?"

presentation_mode:
  aliases: ["presentation mode", "showtime", "screen share mode"]
  description: "Make the screen presentable before you share it."
  say: "Clearing the stage."
  steps:
    - media: pause
    - quit_apps: ["Messages", "Mail", "Slack", "WhatsApp"]
    - hide_others: true
    - volume: 50

focus_sprint:
  aliases: ["focus sprint", "pomodoro", "sprint mode", "start a sprint"]
  description: "Twenty-five minutes of deep work, then I'll call time."
  say: "Twenty-five minutes, starting now. I'll tell you when to stop."
  steps:
    - volume: 25
    - music: "Deep Focus"
    - timer: {minutes: 25, label: "Focus sprint"}

break_time:
  aliases: ["break time", "take a break", "coffee break"]
  description: "Five minutes away from the screen."
  say: "Go and stretch your legs. I'll call you back in five."
  steps:
    - media: pause
    - timer: {minutes: 5, label: "Break"}
    - sleep_display: true

declutter:
  aliases: ["declutter", "clean up my screen", "clear my screen"]
  description: "Hide every window except JARVIS."
  say: "Tidying up."
  steps:
    - hide_others: true

movie_night:
  aliases: ["movie night", "cinema mode", "movie mode"]
  description: "Dim things down and let you drive with your hands from the sofa."
  say: "Movie night. Use your hands to pause or change the volume."
  steps:
    - dark_mode: true
    - brightness: 45
    - volume: 60
    - hand_control: true

hands_on:
  aliases: ["hands on", "gesture mode", "hand control protocol"]
  description: "Switch on hand control: gestures and the air mouse."
  steps:
    - hand_control: true

lockdown:
  aliases: ["lockdown", "lock it down", "i'm heading out", "be right back"]
  description: "Pause everything, mute, and lock the Mac."
  say: "Locking down."
  steps:
    - media: pause
    - volume: 0
    - wait: 1
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
        builtin = yaml.safe_load(DEFAULT_PROTOCOLS) or {}
        try:
            mine = yaml.safe_load(self.path.read_text()) or {}
        except Exception as exc:
            log.error("couldn't parse %s: %s", self.path, exc)
            mine = {}
        merged = {**builtin, **{k: v for k, v in mine.items() if isinstance(v, dict)}}
        self.data = {k: v for k, v in merged.items() if not v.get("disabled")}

    def list(self) -> list[dict]:
        return [{"name": k, "description": v.get("description", ""), "aliases": v.get("aliases", []),
                 "pinned": bool(v.get("pinned"))}
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
            "sleep_display": ("system_action", lambda v: {"action": "sleep_display"}),
        }
        if kind == "wait":
            await asyncio.sleep(float(value))
            return "ok"
        if kind == "timer":
            spec = value if isinstance(value, dict) else {"minutes": value}
            if getattr(ctx, "scheduler", None) is None:
                return "no scheduler"
            ctx.scheduler.add_timer(60 * float(spec.get("minutes", 5)), str(spec.get("label", "")))
            return f"{spec.get('minutes')} minute timer set"
        if kind == "hand_control":
            if ctx.orchestrator is None:
                return "not running"
            return ctx.orchestrator.set_hand_control(bool(value))
        if kind == "quit_apps":
            done = []
            for name in value if isinstance(value, list) else [value]:
                r = await registry.call("quit_application", {"name": str(name)}, ctx)
                done.append(f"{name}: {r.summary}")
            return "; ".join(done)
        if kind == "hide_others":
            r = await registry.call("hide_other_apps", {}, ctx)
            return r.summary
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
