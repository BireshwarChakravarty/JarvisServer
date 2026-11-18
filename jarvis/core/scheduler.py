"""Timers, spoken alarms, and JARVIS's proactive watch: upcoming meetings,
low battery, and anything else worth a polite interruption."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
import uuid
from typing import Awaitable, Callable

from jarvis.events import bus

log = logging.getLogger("jarvis.scheduler")

Announce = Callable[[str, str], Awaitable[None]]  # (text, priority)


class Scheduler:
    def __init__(self, memory, announce: Announce, cfg=None) -> None:
        self.memory = memory
        self.announce = announce
        self.cfg = cfg
        self._tasks: dict[str, asyncio.Task] = {}
        self.alarms: dict[str, dict] = {}
        self._announced_events: set[str] = set()
        self._battery_warned = False
        self.loop: asyncio.AbstractEventLoop | None = None

    # ── running on JARVIS's event loop, whoever calls ───────────────────
    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def _on_loop(self, fn):
        """Run fn() on the event loop and return its result. Tools run in worker threads, where
        asyncio.create_task fails ("no running event loop"): hand the work to the loop instead."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            self.loop = self.loop or running
            if running is self.loop:
                return fn()
        loop = self.loop
        if loop is None or loop.is_closed():
            raise RuntimeError("timers aren't running yet: JARVIS is still starting")

        async def call():
            return fn()
        return asyncio.run_coroutine_threadsafe(call(), loop).result(timeout=10)

    # ── timers & alarms ──────────────────────────────────────────────────
    def add_alarm(self, when: float, message: str, kind: str = "reminder") -> dict:
        alarm_id = uuid.uuid4().hex[:8]
        alarm = {"id": alarm_id, "when": when, "message": message, "kind": kind}
        return self._on_loop(lambda: self._arm(alarm))

    def _arm(self, alarm: dict) -> dict:
        # the countdown first: a timer is only listed (and saved) once it will really go off
        self._tasks[alarm["id"]] = asyncio.create_task(self._fire_later(alarm))
        self.alarms[alarm["id"]] = alarm
        self._persist()
        bus.emit("alarms", alarms=self.list())
        return alarm

    def add_timer(self, seconds: float, label: str = "") -> dict:
        label = label or _describe_duration(seconds)
        return self.add_alarm(time.time() + seconds, label, kind="timer")

    def cancel(self, alarm_id: str | None = None, label: str | None = None) -> int:
        return self._on_loop(lambda: self._cancel(alarm_id, label))

    def _cancel(self, alarm_id: str | None, label: str | None) -> int:
        ids = [alarm_id] if alarm_id else [a["id"] for a in self.alarms.values()
                                           if label and label.lower() in a["message"].lower()]
        if not alarm_id and not label:
            ids = list(self.alarms)
        n = 0
        for i in ids:
            if i in self.alarms:
                self.alarms.pop(i)
                t = self._tasks.pop(i, None)
                if t:
                    t.cancel()
                n += 1
        self._persist()
        bus.emit("alarms", alarms=self.list())
        return n

    def list(self) -> list[dict]:
        now = time.time()
        return sorted(
            ({**a, "in_seconds": int(a["when"] - now)} for a in self.alarms.values()),
            key=lambda a: a["when"],
        )

    async def _fire_later(self, alarm: dict) -> None:
        try:
            await asyncio.sleep(max(0.0, alarm["when"] - time.time()))
        except asyncio.CancelledError:
            return
        self.alarms.pop(alarm["id"], None)
        self._tasks.pop(alarm["id"], None)
        self._persist()
        bus.emit("alarms", alarms=self.list())
        title = self.cfg.get("owner.title", "sir") if self.cfg else "sir"
        if alarm["kind"] == "timer":
            text = f"{title.capitalize()}, your {alarm['message']} timer is done."
        else:
            text = f"{title.capitalize()}, a reminder: {alarm['message']}."
        await self.announce(text, "high")

    def _persist(self) -> None:
        if self.memory:
            self.memory.set("alarms", list(self.alarms.values()))

    def restore(self) -> None:
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if not self.memory:
            return
        for alarm in self.memory.get("alarms", []) or []:
            if alarm["when"] < time.time() - 3600:
                continue  # stale (more than an hour overdue while JARVIS was off)
            self.alarms[alarm["id"]] = alarm
            self._tasks[alarm["id"]] = asyncio.create_task(self._fire_later(alarm))

    # ── proactive watch ──────────────────────────────────────────────────
    async def watch(self) -> None:
        """Every minute: upcoming meetings, battery health."""
        await asyncio.sleep(20)
        while True:
            try:
                await self._check_calendar()
                await self._check_battery()
            except Exception as exc:
                log.debug("watch tick failed: %s", exc)
            await asyncio.sleep(60)

    async def _check_calendar(self) -> None:
        from jarvis.mac.pim import events_between
        from jarvis.mac.system import IS_MAC

        if not IS_MAC:
            return
        now = dt.datetime.now().astimezone()
        events = await asyncio.to_thread(events_between, now, now + dt.timedelta(minutes=11))
        for e in events:
            if e.get("all_day") or not e.get("start"):
                continue
            key = f"{e['title']}@{e['start']}"
            if key in self._announced_events:
                continue
            start = dt.datetime.fromisoformat(e["start"])
            minutes = max(0, round((start - now).total_seconds() / 60))
            if minutes <= 10:
                self._announced_events.add(key)
                where = f" in {e['location']}" if e.get("location") else ""
                when = "now" if minutes == 0 else f"in {minutes} minute{'s' if minutes != 1 else ''}"
                await self.announce(f"Heads up: {e['title']}{where} starts {when}.", "normal")

    async def _check_battery(self) -> None:
        from jarvis.mac.system import battery

        b = await asyncio.to_thread(battery)
        pct, state = b.get("percent"), (b.get("state") or "").lower()
        if pct is None:
            return
        if "discharging" in state and pct <= 15 and not self._battery_warned:
            self._battery_warned = True
            await self.announce(f"Battery is down to {pct} percent. You may want to plug in.", "normal")
        elif "charg" in state and "dis" not in state:
            self._battery_warned = False


def _describe_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} second"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} minute" + (f" {sec} second" if sec else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours} hour" + (f" {minutes} minute" if minutes else "")
