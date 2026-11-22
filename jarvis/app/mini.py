"""Mini JARVIS: a small floating capsule that keeps JARVIS visible while his window is minimised,
closed to the background or hidden with ⌘H. Click it to bring the HUD back; drag it anywhere.

How it behaves (from Apple's window model, tested patterns for floating overlays):
  • a frameless, transparent, always-on-top window that never takes keyboard focus
  • joins every Space and floats over full-screen apps (canJoinAllSpaces + fullScreenAuxiliary)
  • stays up when JARVIS is hidden with ⌘H (setCanHide_(False))
  • stays clear of other overlays: it opens top-centre under the menu bar, inside the screen's
    visible frame (away from notification banners at the top-right, the Dock, Live Captions and
    Stage Manager), never shows alongside the main window, and lets clicks pass straight through
    while the air mouse is driving the pointer
  • small (a 32-pt orb, one status word, one line) and only there while he's doing something:
    it appears when he listens, thinks, speaks or announces, and fades away a few seconds after
    he goes back to standby (app.mini_hide_when_idle: false keeps it up all the time)
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.mini")

W, H = 250, 48
ACTIVE = {"listening", "transcribing", "thinking", "working", "speaking", "error"}
LINGER = 3.5          # seconds it stays after he goes quiet
_panel: "MiniPanel | None" = None


def click_through(active: bool) -> None:
    """Called when the air mouse turns on/off: the capsule must never catch a hand-made click."""
    if _panel is not None:
        _panel.set_click_through(active)


class MiniPanel:
    def __init__(self, cfg, url: str) -> None:
        global _panel
        self.cfg = cfg
        self.url = url
        self.window = None
        self.visible = False
        self.enabled = bool(cfg.get("app.mini_panel", True))
        self.only_when_active = bool(cfg.get("app.mini_hide_when_idle", True))
        self.armed = False          # the main window is minimised / closed / hidden
        self.active = False         # JARVIS is listening, thinking, speaking…
        self._linger = None
        import threading

        self._lock = threading.RLock()
        _panel = self

    # ── creation ─────────────────────────────────────────────────────────
    def create(self) -> None:
        if not self.enabled:
            return
        import webview

        x, y = self._position()
        self.window = webview.create_window(
            "JARVIS mini", self.url, width=W, height=H, x=x, y=y, resizable=False, frameless=True,
            easy_drag=True, on_top=True, focus=False, transparent=True, shadow=False, hidden=True,
            background_color="#000000", text_select=False,
        )
        self.window.events.loaded += self._native_setup
        self.window.events.moved += self._remember

    def _position(self) -> tuple[int | None, int | None]:
        saved = self.cfg.get("app.mini_position")
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            return int(saved[0]), int(saved[1])
        try:
            import AppKit

            vf = AppKit.NSScreen.mainScreen().visibleFrame()          # excludes menu bar and Dock
            full = AppKit.NSScreen.mainScreen().frame()
            x = vf.origin.x + (vf.size.width - W) / 2
            top_inset = full.size.height - (vf.origin.y + vf.size.height)   # menu bar height
            return int(x), int(top_inset + 10)                            # pywebview: y from the top
        except Exception:
            return None, None

    def _native_setup(self) -> None:
        def apply():
            try:
                import AppKit

                w = self.window.native
                w.setCollectionBehavior_(
                    AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                    | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                    | AppKit.NSWindowCollectionBehaviorStationary
                    | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
                w.setLevel_(AppKit.NSStatusWindowLevel)
                w.setHidesOnDeactivate_(False)
                w.setCanHide_(False)            # still visible after ⌘H hides JARVIS
                w.setHasShadow_(False)
                w.setExcludedFromWindowsMenu_(True)
            except Exception as exc:
                log.info("mini panel: native setup skipped: %s", exc)

        _main(apply)

    def _remember(self, x=None, y=None) -> None:
        # moved fires continuously while dragging: save once it settles
        import threading

        if getattr(self, "_save_timer", None):
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.8, self._save, (x, y))
        self._save_timer.daemon = True
        self._save_timer.start()

    def _save(self, x=None, y=None) -> None:
        try:
            if x is None:
                x, y = self.window.x, self.window.y
            from jarvis.app.setup_api import _write_user_config

            _write_user_config(self.cfg, {"app": {"mini_position": [int(x), int(y)]}})
        except Exception:
            log.debug("couldn't save mini position", exc_info=True)

    # ── show / hide ──────────────────────────────────────────────────────
    # show()/hide() say whether the main window is out of the way; set_active() says whether JARVIS is
    # doing anything. The capsule is on screen only when both are true (plus a short linger).
    def show(self) -> None:
        with self._lock:
            self.armed = True
            self._apply()

    def hide(self) -> None:
        with self._lock:
            self.armed = False
            self._apply()

    def set_active(self, on: bool) -> None:
        import threading

        with self._lock:
            if self._linger is not None:
                self._linger.cancel()
                self._linger = None
            if on:
                self.active = True
                self._apply()
            elif self.active:
                def quiet():
                    with self._lock:
                        self.active = False
                        self._linger = None
                        self._apply()
                self._linger = threading.Timer(LINGER, quiet)
                self._linger.daemon = True
                self._linger.start()

    def wanted(self) -> bool:
        return self.enabled and self.armed and (self.active or not self.only_when_active)

    def _apply(self) -> None:
        if self.wanted():
            self._show_window()
        else:
            self._hide_window()

    def _show_window(self) -> None:
        if self.window is None or self.visible or not self.enabled:
            return
        self.visible = True
        try:
            self.window.show()
            self._native_setup()
            _main(lambda: self.window.native.orderFrontRegardless())   # without activating JARVIS
        except Exception as exc:
            log.debug("mini show: %s", exc)

    def _hide_window(self) -> None:
        if self.window is None or not self.visible:
            return
        self.visible = False
        try:
            self.window.hide()
        except Exception as exc:
            log.debug("mini hide: %s", exc)

    def set_click_through(self, active: bool) -> None:
        if self.window is None:
            return
        _main(lambda: self.window.native.setIgnoresMouseEvents_(bool(active)))


def _main(fn) -> None:
    try:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(fn)
    except Exception:
        fn()


def watch_app_hide(panel: MiniPanel) -> None:
    """⌘H hides every JARVIS window except the capsule: show it then, hide it on unhide."""
    try:
        import AppKit
        import objc
        from Foundation import NSObject

        class _HideObserver(NSObject):
            def initWithPanel_(self, p):
                self = objc.super(_HideObserver, self).init()
                self.panel = p
                return self

            def didHide_(self, note):
                self.panel.show()

            def didUnhide_(self, note):
                self.panel.hide()

        obs = _HideObserver.alloc().initWithPanel_(panel)
        nc = AppKit.NSNotificationCenter.defaultCenter()
        nc.addObserver_selector_name_object_(obs, "didHide:", AppKit.NSApplicationDidHideNotification, None)
        nc.addObserver_selector_name_object_(obs, "didUnhide:", AppKit.NSApplicationDidUnhideNotification, None)
        panel._observer = obs   # keep it alive
    except Exception as exc:
        log.info("mini panel: app-hide watch skipped: %s", exc)
