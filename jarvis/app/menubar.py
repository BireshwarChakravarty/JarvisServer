"""Menu-bar presence (the little reactor icon top-right) and Dock behaviour.

Closing the window just hides it — JARVIS keeps listening. Click the Dock icon or the
menu-bar icon to bring him back; ⌘Q or "Quit JARVIS" quits."""
from __future__ import annotations

import logging
import os

log = logging.getLogger("jarvis.menubar")

_target = None   # keep strong references, or PyObjC objects get collected
_item = None


def is_quit_shortcut() -> bool:
    """True when the current Cocoa event is ⌘Q (so closing means quit, not hide)."""
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
            (None, None, None),
            ("Settings…", "settings", ","),
            (None, None, None),
            ("Quit JARVIS", "quit", "q"),
        ):
            if title is None:
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "run:", shortcut)
            mi.setTarget_(_target)
            mi.setRepresentedObject_(key)
            menu.addItem_(mi)
        _item.setMenu_(menu)
    except Exception as exc:
        log.warning("menu bar item unavailable: %s", exc)


def quit_app() -> None:
    os._exit(0)
