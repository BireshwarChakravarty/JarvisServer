"""JARVIS's hands on the Mac: click, type, press keys and scroll, like a person at the keyboard.

Used by "do it for me": JARVIS finds the item with the same finder the on-screen guidance uses
(jarvis/mac/screen_elements.py), brings its app to the front, shows the ring on it for a moment so you
can see what he's about to do, then acts. Clicks on anything consequential ("Send", "Delete", "Buy",
"Pay", "Sign out"…) are asked about first, out loud, like every other risky action.

Coordinates are global screen points, origin top-left of the main display (what Accessibility and
Quartz events both use). Posting events needs the Accessibility permission JARVIS already asks for.
"""
from __future__ import annotations

import re
import subprocess
import time

# Clicking or pressing these does something you can't simply undo, or that reaches other people.
RISKY = re.compile(
    r"\b(send|delete|remove|erase|trash|bin|buy|purchase|pay|order|checkout|check out|subscribe|unsubscribe|"
    r"submit|transfer|confirm|sign ?out|log ?out|uninstall|format|empty|discard|reset|post|publish|share|"
    r"invite|accept|decline|reply all|forward|install|allow|grant|approve|book|reserve|donate|withdraw)\b", re.IGNORECASE)

KEYCODES = {
    "return": 36, "enter": 76, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
    "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111,
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
}
MODIFIERS = {"cmd": "command", "command": "command", "⌘": "command", "shift": "shift", "⇧": "shift",
             "alt": "option", "option": "option", "opt": "option", "⌥": "option", "ctrl": "control",
             "control": "control", "⌃": "control", "fn": "fn"}
RISKY_KEYS = {("command", "delete"), ("command", "backspace"), ("command", "q")}


def is_risky(label: str) -> bool:
    return bool(RISKY.search(label or ""))


def parse_keys(combo: str) -> tuple[set[str], str]:
    """'cmd+shift+t' → ({'command', 'shift'}, 't'). Raises ValueError for keys it doesn't know."""
    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*|\s+", (combo or "").strip()) if p.strip()]
    if not parts:
        raise ValueError("no key given")
    mods, key = set(), None
    for p in parts:
        if p in MODIFIERS:
            mods.add(MODIFIERS[p])
        elif key is None and (p in KEYCODES or len(p) == 1):
            key = p
        else:
            raise ValueError(f"unknown key {p!r}")
    if key is None:
        raise ValueError("only modifiers given")
    if key not in KEYCODES:
        raise ValueError(f"unknown key {key!r}")
    return mods, key


def risky_keys(combo: str) -> bool:
    try:
        mods, key = parse_keys(combo)
    except ValueError:
        return False
    return any(m in mods and key == k for m, k in RISKY_KEYS)


# ── macOS events ───────────────────────────────────────────────────────────
def _Q():
    import Quartz

    return Quartz


def _flags(mods: set[str]) -> int:
    Q = _Q()
    f = 0
    for m, flag in (("command", Q.kCGEventFlagMaskCommand), ("shift", Q.kCGEventFlagMaskShift),
                    ("option", Q.kCGEventFlagMaskAlternate), ("control", Q.kCGEventFlagMaskControl),
                    ("fn", Q.kCGEventFlagMaskSecondaryFn)):
        if m in mods:
            f |= flag
    return f


def click(x: float, y: float, double: bool = False, right: bool = False) -> None:
    Q = _Q()
    pt = (float(x), float(y))
    down, up, btn = ((Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight) if right else
                     (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft))
    Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, pt, btn))
    time.sleep(0.05)
    for n in ((1, 2) if double else (1,)):
        for kind in (down, up):
            ev = Q.CGEventCreateMouseEvent(None, kind, pt, btn)
            Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, n)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)
            time.sleep(0.02)
        time.sleep(0.06)


def press(combo: str) -> None:
    Q = _Q()
    mods, key = parse_keys(combo)
    code, flags = KEYCODES[key], _flags(mods)
    for is_down in (True, False):
        ev = Q.CGEventCreateKeyboardEvent(None, code, is_down)
        if flags:
            Q.CGEventSetFlags(ev, flags)
        Q.CGEventPost(Q.kCGHIDEventTap, ev)
        time.sleep(0.02)


def type_text(text: str) -> None:
    """Type any text (any language, emoji) as keystrokes, independent of the keyboard layout."""
    Q = _Q()
    for i in range(0, len(text), 20):                     # the event carries at most ~20 characters
        chunk = text[i:i + 20]
        for is_down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(None, 0, is_down)
            Q.CGEventKeyboardSetUnicodeString(ev, len(chunk), chunk)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)
        time.sleep(0.01)


def scroll(direction: str = "down", amount: int = 5) -> None:
    Q = _Q()
    n = max(1, min(int(amount or 5), 50))
    dy = -n if direction == "down" else n if direction == "up" else 0
    dx = -n if direction == "right" else n if direction == "left" else 0
    ev = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 2, dy, dx)
    Q.CGEventPost(Q.kCGHIDEventTap, ev)


def bring_to_front(pid: int | None, app_name: str = "") -> bool:
    """Put the app the item belongs to in front, so the click lands on it (not on JARVIS's window)."""
    try:
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication, NSWorkspace

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid)) if pid else None
        if app is not None:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            for _ in range(15):
                front = NSWorkspace.sharedWorkspace().frontmostApplication()
                if front is not None and int(front.processIdentifier()) == int(pid):
                    return True
                time.sleep(0.05)
            bundle = app.bundleIdentifier()
            if bundle:                                    # LaunchServices activates reliably where that didn't
                subprocess.run(["open", "-b", str(bundle)], capture_output=True, timeout=10)
                time.sleep(0.3)
                return True
        if app_name:
            subprocess.run(["open", "-a", app_name], capture_output=True, timeout=10)
            time.sleep(0.3)
            return True
    except Exception:
        pass
    return False
