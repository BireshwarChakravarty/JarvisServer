"""Menu-bar presence (the little reactor icon top-right) and Dock behaviour.

Closing the window just hides it — JARVIS keeps listening. Click the Dock icon or the
menu-bar icon to bring him back; ⌘Q or "Quit JARVIS" quits."""
from __future__ import annotations

import logging
import os

log = logging.getLogger("jarvis.menubar")

_target = None   # keep strong references, or PyObjC objects get collected
_item = None


_terminating = False


def is_quit_shortcut() -> bool:
    """True when JARVIS should really quit rather than hide: ⌘Q, a Quit Apple Event (the system sends
    one at shutdown, restart and log-out; installers and `osascript … quit` do too) or power-off."""
    if _terminating:
        return True
    try:
        import AppKit

        ev = AppKit.NSApp.currentEvent()
        if ev is None or ev.type() != AppKit.NSEventTypeKeyDown:
            return False
        cmd = bool(ev.modifierFlags() & AppKit.NSEventModifierFlagCommand)
        return cmd and (ev.charactersIgnoringModifiers() or "").lower() == "q"
    except Exception:
        return False


def install_reopen_handler(on_reopen) -> None:
    """Make a Dock-icon click re-show the hidden window (pywebview doesn't by default)."""
    try:
        import objc
        from webview.platforms.cocoa import BrowserView

        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
            on_reopen()
            return True

        objc.classAddMethods(BrowserView.AppDelegate, [
            objc.selector(applicationShouldHandleReopen_hasVisibleWindows_, signature=b"Z@:@Z"),
        ])
    except Exception as exc:
        log.info("dock reopen handler unavailable: %s", exc)


def install(actions: dict) -> None:
    """Create the status-bar item. Must run on the main thread (use AppHelper.callAfter)."""
    global _target, _item
    try:
        import AppKit
        import objc
        from Foundation import NSObject

        class JarvisMenuTarget(NSObject):
            def initWithActions_(self, acts):
                self = objc.super(JarvisMenuTarget, self).init()
                if self is None:
                    return None
                self.acts = acts
                return self

            def run_(self, sender):
                fn = self.acts.get(sender.representedObject())
                if fn:
                    try:
                        fn()
                    except Exception as exc:  # pragma: no cover
                        log.error("menu action failed: %s", exc)

        _target = JarvisMenuTarget.alloc().initWithActions_(actions)
        bar = AppKit.NSStatusBar.systemStatusBar()
        _item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        button = _item.button()
        img = None
        if hasattr(AppKit.NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
            img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("circle.hexagongrid.circle", "JARVIS") \
                or AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("circle.circle", "JARVIS")
        if img is not None:
            img.setTemplate_(True)
            button.setImage_(img)
        else:
            button.setTitle_("J")
        button.setToolTip_("JARVIS")

        menu = AppKit.NSMenu.alloc().init()
        for title, key, shortcut in (
            ("Show JARVIS", "show", ""),
            ("Listen", "listen", ""),
            ("Mute Microphone", "mute", ""),
            ("Unmute Microphone", "unmute", ""),
            ("Hand Control On / Off", "hands", ""),
            ("Pair iPhone or iPad…", "pair", ""),
            ("Learn My Voice…", "learn_voice", ""),
            ("Theme", "themes", ""),
            (None, None, None),
            ("Settings…", "settings", ","),
            ("Update JARVIS…", "update", ""),
            (None, None, None),
            ("Quit JARVIS", "quit", "q"),
        ):
            if title is None:
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            if key == "themes":                     # a submenu with the four looks
                from jarvis.core import themes

                mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
                sub = AppKit.NSMenu.alloc().initWithTitle_(title)
                for tid, info in themes.THEMES.items():
                    t = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(info["name"], "run:", "")
                    t.setTarget_(_target)
                    t.setRepresentedObject_(f"theme:{tid}")
                    sub.addItem_(t)
                mi.setSubmenu_(sub)
                menu.addItem_(mi)
                continue
            mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "run:", shortcut)
            mi.setTarget_(_target)
            mi.setRepresentedObject_(key)
            menu.addItem_(mi)
        _item.setMenu_(menu)
    except Exception as exc:
        log.warning("menu bar item unavailable: %s", exc)


_monitors: list = []
RIGHT_OPTION = 61  # kVK_RightOption


def install_push_to_talk(on_change) -> None:
    """Hold right ⌥ to talk. Uses NSEvent monitors on the main thread (pynput's keyboard hook
    is unsafe inside a Cocoa app on recent macOS). Needs Accessibility for the global monitor;
    the local monitor works whenever the JARVIS window is focused."""
    try:
        import AppKit

        state = {"down": False}
        mask = AppKit.NSEventMaskFlagsChanged

        def handle(event):
            try:
                if event.keyCode() == RIGHT_OPTION:
                    down = bool(event.modifierFlags() & AppKit.NSEventModifierFlagOption)
                    if down != state["down"]:
                        state["down"] = down
                        on_change(down)
            except Exception:
                pass

        def local(event):
            handle(event)
            return event

        _monitors.append(AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, handle))
        _monitors.append(AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, local))
    except Exception as exc:
        log.info("push-to-talk unavailable: %s", exc)


def quit_app() -> None:
    os._exit(0)


def install_quit_handlers() -> None:
    """Honour quit requests from outside. Without this, closing-means-hide also swallowed the system's
    own quit at shutdown, restart and log-out, so JARVIS could cancel your Mac shutting down."""
    global _quit_target
    try:
        import struct

        import AppKit
        from Foundation import NSObject

        def fourcc(code: bytes) -> int:
            return struct.unpack(">I", code)[0]

        class QuitTarget(NSObject):
            def handleQuit_withReply_(self, event, reply):
                _really_quit()

            def willPowerOff_(self, note):
                _really_quit()

        _quit_target = QuitTarget.alloc().init()
        AppKit.NSAppleEventManager.sharedAppleEventManager().setEventHandler_andSelector_forEventClass_andEventID_(
            _quit_target, "handleQuit:withReply:", fourcc(b"aevt"), fourcc(b"quit"))
        AppKit.NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            _quit_target, "willPowerOff:", AppKit.NSWorkspaceWillPowerOffNotification, None)
    except Exception as exc:
        log.info("quit handlers not installed: %s", exc)


_quit_target = None


def mark_terminating() -> None:
    """From here on, closing means quitting (the app is shutting down)."""
    global _terminating
    _terminating = True


def _really_quit() -> None:
    mark_terminating()
    quit_app()
