"""The click-through layer JARVIS draws on to show you where to click.

A frameless, transparent window covering the display that holds the target, above every other
window (including open menus and full-screen apps), that never takes focus and lets every click
pass straight through to the app underneath. It draws /guide (guide.html) and is moved to whichever
display the target is on.

Window calls are queued to one helper thread: pywebview waits on the main thread, and JARVIS's
event loop must never wait with it.
"""
from __future__ import annotations

import json
import logging
import queue
import threading

log = logging.getLogger("jarvis.guide.overlay")


def screen_for(rect, screens):
    """The display (x, y, w, h in global top-left points) holding the rectangle's centre."""
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    for s in screens:
        if s[0] <= cx < s[0] + s[2] and s[1] <= cy < s[1] + s[3]:
            return s
    return screens[0] if screens else (0.0, 0.0, 1440.0, 900.0)


def to_cocoa(frame, main_height):
    """Global top-left frame → Cocoa (bottom-left) frame for NSWindow.setFrame."""
    x, y, w, h = frame
    return x, main_height - y - h, w, h


def local(rect, screen):
    """The target's rectangle relative to the overlay covering `screen`."""
    return rect[0] - screen[0], rect[1] - screen[1], rect[2], rect[3]


def mouse_state():
    """(x, y, left button down) in global top-left points. Needs no permission."""
    import AppKit

    loc = AppKit.NSEvent.mouseLocation()
    main_h = AppKit.NSScreen.screens()[0].frame().size.height
    return float(loc.x), float(main_h - loc.y), bool(AppKit.NSEvent.pressedMouseButtons() & 1)


class GuideOverlay:
    def __init__(self, url: str) -> None:
        self.url = url
        self.window = None
        self._q: queue.Queue = queue.Queue()
        self._screen = None
        threading.Thread(target=self._worker, daemon=True, name="guide-overlay").start()

    def create(self) -> None:
        import webview

        self.window = webview.create_window(
            "JARVIS guide", self.url, width=800, height=600, x=0, y=0, resizable=False, frameless=True,
            easy_drag=False, on_top=True, focus=False, transparent=True, shadow=False, hidden=True,
            background_color="#000000", text_select=False)

    # ── API used by jarvis.core.guide ────────────────────────────────────
    def show(self, rect, caption: str = "", step: int = 1, total: int = 1, style: str = "full", kind: str = "") -> None:
        self._q.put(("show", (rect, caption, step, total, style, kind)))

    def done(self, ok: bool) -> None:
        self._q.put(("done", ok))

    def hide(self) -> None:
        self._q.put(("hide", None))

    # ── the helper thread ───────────────────────────────────────────────
    def _worker(self) -> None:
        while True:
            kind, arg = self._q.get()
            try:
                if self.window is None:
                    continue
                if kind == "show":
                    self._show(*arg)
                elif kind == "done":
                    self.window.evaluate_js(f"guide.done({'true' if arg else 'false'})")
                    threading.Event().wait(0.9)
                    if self._q.empty():
                        self._order_out()
                elif kind == "hide":
                    self.window.evaluate_js("guide.hide()")
                    self._order_out()
            except Exception as exc:
                log.debug("guide overlay %s failed: %s", kind, exc)

    def _show(self, rect, caption, step, total, style, kind) -> None:
        from jarvis.mac.screen_elements import screens

        all_screens = screens()
        scr = screen_for(rect, all_screens)
        if scr != self._screen:
            self._screen = scr
            main_h = all_screens[0][3] if all_screens else scr[3]
            self._place(to_cocoa(scr, main_h))
            self.window.evaluate_js("guide.reset()")
        x, y, w, h = local(rect, scr)
        payload = {"x": x, "y": y, "w": w, "h": h, "caption": caption, "step": step, "total": total,
                   "style": style, "kind": kind}
        self._front()
        self.window.evaluate_js(f"guide.show({json.dumps(payload)})")

    def _place(self, cocoa_frame) -> None:
        done = threading.Event()

        def apply():
            try:
                import AppKit
                import Quartz

                w = self.window.native
                x, y, fw, fh = cocoa_frame
                w.setFrame_display_(AppKit.NSMakeRect(x, y, fw, fh), True)
                w.setIgnoresMouseEvents_(True)
                w.setLevel_(Quartz.CGWindowLevelForKey(Quartz.kCGAssistiveTechHighWindowLevelKey))
                w.setCollectionBehavior_(
                    AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                    | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                    | AppKit.NSWindowCollectionBehaviorStationary
                    | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
                w.setHidesOnDeactivate_(False)
                w.setCanHide_(False)
                w.setHasShadow_(False)
                w.setExcludedFromWindowsMenu_(True)
                w.setOpaque_(False)
                w.setBackgroundColor_(AppKit.NSColor.clearColor())
            except Exception as exc:
                log.info("guide overlay: native setup failed: %s", exc)
            finally:
                done.set()

        _main(apply)
        done.wait(3)

    def _front(self) -> None:
        _main(lambda: self.window.native.orderFrontRegardless())     # shown without activating JARVIS

    def _order_out(self) -> None:
        _main(lambda: self.window.native.orderOut_(None))


def _main(fn) -> None:
    try:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(fn)
    except Exception:
        fn()
