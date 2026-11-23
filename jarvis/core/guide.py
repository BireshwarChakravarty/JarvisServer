"""On-screen guidance: JARVIS shows you where to click, then steps back as you learn.

  "How do I add a total in Numbers?"  →  he finds the button, flies a ring to it, says why in a
  sentence, waits for your click, then the next step. The walkthrough is remembered:

    first time        full: the ring, a caption and his voice
    next one or two   a hint: just the ring and a word or two, no speech
    after that        you've got it: he only says which step, unless you ask to be shown

Platform pieces are injected: `find` / `resolve` (screen_elements), `overlay` (the click-through
window that draws the ring), `mouse` (where the pointer is, is the button down). That keeps this
file testable anywhere.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from jarvis.events import bus

log = logging.getLogger("jarvis.guide")

STORE_KEY = "walkthroughs"
HINT_AFTER = 1        # completed this many times → hints only
SOLO_AFTER = 3        # → "you've got this"


def task_key(app: str, task: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", (task or "").lower())
    t = " ".join(w for w in t.split() if w not in {"how", "do", "i", "to", "the", "a", "an", "in", "my", "can", "you",
                                                     "show", "me", "please"})
    return f"{(app or '').lower().strip()}|{t}"


def style_for(completed: int) -> str:
    if completed >= SOLO_AFTER:
        return "solo"
    if completed >= HINT_AFTER:
        return "hint"
    return "full"


class Guide:
    def __init__(self, memory, speaker=None, find: Callable | None = None, resolve: Callable | None = None,
                 overlay=None, mouse: Callable | None = None, frontmost: Callable | None = None,
                 hands=None) -> None:
        self.memory = memory
        self.hands = hands                  # jarvis.mac.control (click / type / press), injected in tests
        self.speaker = speaker
        self._find, self._resolve = find, resolve
        self.overlay = overlay
        self.mouse = mouse                  # () -> (x, y, left_button_down) in global top-left points
        self.frontmost = frontmost or (lambda: "")
        self._cancel: asyncio.Event | None = None

    # ── memory ───────────────────────────────────────────────────────────
    def _all(self) -> dict:
        return dict(self.memory.get(STORE_KEY, {}) or {})

    def known(self, task: str, app: str = "") -> dict:
        rec = self._all().get(task_key(app or self.frontmost(), task))
        if not rec:
            return {"known": False, "completed": 0, "style": "full", "steps": []}
        return {"known": True, "completed": rec.get("completed", 0), "style": style_for(rec.get("completed", 0)),
                "steps": rec.get("steps", []), "app": rec.get("app", "")}

    def _record(self, app: str, task: str, step: int, total: int, label: str, done: bool) -> int:
        allw = self._all()
        k = task_key(app, task)
        rec = allw.get(k) or {"task": task, "app": app, "steps": [], "completed": 0}
        steps = list(rec.get("steps", []))
        while len(steps) < step:
            steps.append("")
        steps[step - 1] = label
        rec["steps"] = steps[:max(total, step)]
        if done:
            rec["completed"] = int(rec.get("completed", 0)) + 1
        rec["last"] = time.time()
        allw[k] = rec
        self.memory.set(STORE_KEY, allw)
        return rec["completed"]

    def forget(self, task: str = "", app: str = "") -> str:
        if not task:
            self.memory.set(STORE_KEY, {})
            return "Forgotten every walkthrough."
        allw = self._all()
        allw.pop(task_key(app or self.frontmost(), task), None)
        self.memory.set(STORE_KEY, allw)
        return "Forgotten. I'll show you properly next time."

    # ── finding ──────────────────────────────────────────────────────────
    async def find(self, query: str) -> list[dict]:
        if self._find is None:
            return []
        return await asyncio.to_thread(self._find, query)

    # ── pointing ─────────────────────────────────────────────────────────
    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.set()

    async def point(self, target: str, say: str = "", task: str = "", step: int = 1, total: int = 1,
                    style: str = "auto", wait: bool = True, timeout: float = 60.0) -> dict[str, Any]:
        if self._resolve is None or self.overlay is None:
            return {"status": "unavailable", "why": "On-screen guidance needs the Mac app."}
        el = await asyncio.to_thread(self._resolve, target)
        if el is None:
            return {"status": "not_found", "target": target,
                    "advice": "Not visible right now. Ask them to open the right window or menu, or describe "
                              "where it usually is."}
        app = el.app or self.frontmost()
        fam = self.known(task, app) if task else {"completed": 0, "style": "full"}
        use = style if style in ("full", "hint") else ("full" if fam["style"] == "full" else "hint")
        caption = say if use == "full" else (el.label[:40] if el.label else say[:40])
        rect = (el.x, el.y, el.w, el.h)
        self.overlay.show(rect, caption=caption, step=step, total=total, style=use, kind=el.kind)
        bus.emit("guide", state="pointing", label=el.label, step=step, total=total, style=use)
        if use == "full" and say and self.speaker is not None:
            self.speaker.enqueue(say)
        result = "shown"
        try:
            if wait and self.mouse is not None:
                result = await self._wait_click(rect, timeout)
        finally:
            self.overlay.done(result == "clicked") if hasattr(self.overlay, "done") else self.overlay.hide()
            bus.emit("guide", state=result, label=el.label, step=step, total=total)
        completed = fam.get("completed", 0)
        if task and result in ("clicked", "shown"):
            completed = self._record(app, task, step, total, el.label, done=(step >= total and result == "clicked"))
        out = {"status": result, "target": {"label": el.label, "kind": el.kind, "where": el.area, "app": app},
               "style": use, "times_completed": completed}
        if step >= total and result == "clicked" and style_for(completed) == "solo":
            out["note"] = "They've done this several times now: next time, offer to let them try it alone."
        return out

    # ── doing it for you ─────────────────────────────────────────────────
    def _hands(self):
        if self.hands is None:
            from jarvis.mac import control

            self.hands = control
        return self.hands

    async def act(self, target: str, action: str = "click", text: str = "", double: bool = False,
                  submit: bool = False, confirm: Callable | None = None) -> dict:
        """Click an item (or click into a field and type), showing the ring on it first so you can
        see what he's doing. One action per call; look again (find) before the next."""
        if self._resolve is None:
            return {"status": "unavailable", "why": "Acting on screen needs the Mac app."}
        el = await asyncio.to_thread(self._resolve, target)
        if el is None:
            return {"status": "not_found", "target": target,
                    "advice": "Not visible right now. Open the right window or menu first (find_on_screen shows what "
                              "is there), or use a direct tool such as open_application."}
        hands = self._hands()
        if confirm is not None:
            risky = (action == "click" and hands.is_risky(el.label)) or (action == "type" and submit)
            if risky:
                what = (f"click “{el.label}”" if action == "click" else
                        f"type “{text[:80]}” into {el.label} and press Return") + (f" in {el.app}" if el.app else "")
                if not await confirm(what):
                    return {"status": "declined", "did": "nothing", "why": f"The user said no to: {what}. Don't retry."}
        pid = (getattr(el, "extra", None) or {}).get("pid")
        await asyncio.to_thread(hands.bring_to_front, pid, el.app)
        rect = (el.x, el.y, el.w, el.h)
        verb = {"click": "Clicking", "type": "Typing into"}.get(action, "Using")
        if self.overlay is not None:
            self.overlay.show(rect, caption=f"{verb} {el.label[:40]}", style="hint", kind=el.kind)
        bus.emit("guide", state="acting", label=el.label, action=action)
        await asyncio.sleep(0.45)                       # long enough to see where he's about to click
        try:
            cx, cy = el.x + el.w / 2, el.y + el.h / 2
            await asyncio.to_thread(hands.click, cx, cy, double)
            if action == "type":
                await asyncio.sleep(0.15)
                await asyncio.to_thread(hands.type_text, text)
                if submit:
                    await asyncio.sleep(0.1)
                    await asyncio.to_thread(hands.press, "return")
        finally:
            if self.overlay is not None:
                self.overlay.done(True) if hasattr(self.overlay, "done") else self.overlay.hide()
        bus.emit("guide", state="acted", label=el.label, action=action)
        did = {"click": f"clicked {el.label}", "type": f"typed into {el.label}" + (" and pressed Return" if submit else "")}
        return {"status": "done", "did": did.get(action, action),
                "target": {"label": el.label, "kind": el.kind, "where": el.area, "app": el.app},
                "next": "Look again with find_on_screen (or look_at_screen) before the next step: things move."}

    async def _wait_click(self, rect, timeout: float, pad: float = 8.0) -> str:
        x, y, w, h = rect
        self._cancel = cancel = asyncio.Event()
        end = time.monotonic() + timeout
        was_down = True                       # ignore a press that was already held when we started
        try:
            while time.monotonic() < end:
                if cancel.is_set():
                    return "cancelled"
                mx, my, down = await asyncio.to_thread(self.mouse)
                if down and not was_down and x - pad <= mx <= x + w + pad and y - pad <= my <= y + h + pad:
                    return "clicked"
                was_down = down
                await asyncio.sleep(1 / 30)
            return "timeout"
        finally:
            self._cancel = None
