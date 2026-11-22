"""JARVIS.app entry point: one native window (onboarding first, then the HUD), the
assistant running in the background, a menu-bar icon, and single-instance behaviour."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import threading
import time

log = logging.getLogger("jarvis.app")


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _wait_port(host: str, port: int, timeout: float = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _port_open(host, port):
            return True
        time.sleep(0.1)
    return False


def allow_app_termination() -> None:
    """Never stand in the way of the app quitting.

    pywebview answers macOS's applicationShouldTerminate: by asking each window's `closing` handler,
    and ours says "hide, don't close" so the red button keeps JARVIS running in the background. That
    answer also reached shutdown, restart and log-out, so macOS showed "JARVIS interrupted shut down…
    try again". Closing a window and quitting the app are different questions: the red button goes
    through windowShouldClose: (still hides), while app termination now always proceeds."""
    try:
        import Foundation
        from webview.platforms import cocoa

        from jarvis.app import menubar

        def applicationShouldTerminate_(self, app):
            menubar.mark_terminating()
            return Foundation.YES

        cocoa.BrowserView.AppDelegate.applicationShouldTerminate_ = applicationShouldTerminate_
    except Exception as exc:
        log.warning("couldn't install the quit fix: %s", exc)


def run_app() -> None:
    os.environ.setdefault("JARVIS_APP", "1")
    # Camera permission is requested natively during onboarding; OpenCV's own request can't spin
    # the main run loop from a worker thread inside a Cocoa app.
    os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")
    from jarvis.__main__ import setup_logging
    from jarvis.config import load_config

    cfg = load_config()
    setup_logging(False)
    log.info("JARVIS.app starting (python %s, data in %s)", sys.version.split()[0], cfg.home)
    host, port = "127.0.0.1", int(cfg.get("hud.port", 7777))

    if _port_open(host, port):  # already running: bring that instance forward
        import httpx

        try:
            me = httpx.get(f"http://{host}:{port}/api/status", timeout=3).json()
            ours = isinstance(me, dict) and "capabilities" in me and "running" in me
        except Exception:
            ours = False
        if ours:
            try:
                httpx.post(f"http://{host}:{port}/api/app/show", timeout=3)
            except Exception:
                pass
            return
        # something else holds JARVIS's port: say so instead of vanishing without a word
        log.error("port %s is in use by another program; JARVIS can't start", port)
        try:
            from jarvis.mac.system import notify

            notify("JARVIS can't start", f"Another app is using port {port}. Quit it, or set hud.port in "
                                         "JARVIS's config.yaml to a different number.")
        except Exception:
            pass
        return

    from jarvis.app.supervisor import Supervisor
    from jarvis.hud.server import create_app, serve

    sup = Supervisor(cfg)

    def core() -> None:
        async def main() -> None:
            await asyncio.gather(serve(create_app(supervisor=sup), host, port), sup.boot())

        try:
            asyncio.run(main())
        except Exception:
            log.exception("core crashed")

    threading.Thread(target=core, daemon=True, name="jarvis-core").start()
    if not _wait_port(host, port):
        log.error("server failed to start")

    import webview

    from jarvis.app import menubar
    from jarvis.core import themes

    allow_app_termination()

    window = webview.create_window(
        "JARVIS", f"http://{host}:{port}/",
        width=1480, height=920, min_size=(980, 640), background_color=themes.BACKGROUND[themes.current(sup.cfg)],
        text_select=False,
    )
    sup.window = window

    # Mini JARVIS: a floating capsule while the window is minimised, closed or hidden (⌘H)
    from jarvis.app.mini import MiniPanel

    mini = MiniPanel(cfg, f"http://{host}:{port}/mini")
    mini.create()
    sup.mini = mini

    # On-screen guidance: the click-through layer he draws on to show you where to click
    from jarvis.app.guide_overlay import GuideOverlay

    guide_overlay = GuideOverlay(f"http://{host}:{port}/guide")
    guide_overlay.create()
    sup.guide_overlay = guide_overlay

    def on_closing():
        if menubar.is_quit_shortcut():
            return True   # ⌘Q: really quit
        window.hide()     # red button: keep running in the background
        if sup.setup_done():
            mini.show()
        return False

    window.events.closing += on_closing
    window.events.minimized += lambda: sup.setup_done() and mini.show()
    window.events.restored += mini.hide
    window.events.shown += mini.hide

    def show():
        sup.show_window()
        try:
            import AppKit

            AppKit.NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            pass

    def hud(msg: dict):
        j = sup.jarvis
        if j is not None and sup.loop is not None:
            asyncio.run_coroutine_threadsafe(j.hud_command(msg), sup.loop)

    actions = {
        "show": show,
        "listen": lambda: (show(), hud({"type": "listen"})),
        "mute": lambda: hud({"type": "mute", "muted": True}),
        "unmute": lambda: hud({"type": "mute", "muted": False}),
        "hands": lambda: hud({"type": "hand_control"}),
        "learn_voice": lambda: (show(), hud({"type": "learn_voice"})),
        "pair": lambda: (sup.show_window("/pair"), show()),
        "update": lambda: update_now(),
        "settings": lambda: (sup.show_window("/setup"), show()),
        "quit": menubar.quit_app,
    }
    for key in themes.THEMES:                      # Theme ▸ Arc Reactor / Dot Matrix / Midnight Glass / Stark
        actions[f"theme:{key}"] = (lambda k=key: themes.apply(sup.cfg, k))
    def update_now():
        from jarvis.app.updater import start_update
        from jarvis.mac.system import notify

        try:
            notify("JARVIS", "Updating to the latest version. I'll be back in a minute or two.")
        except Exception:
            pass
        start_update(cfg.home)

    menubar.install_reopen_handler(show)

    def ptt(down: bool):
        j = sup.jarvis
        if j is not None:
            j.ptt_event(down)

    def after_start():
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(menubar.install, actions)
            AppHelper.callAfter(menubar.install_quit_handlers)
            from jarvis.app.mini import watch_app_hide

            AppHelper.callAfter(watch_app_hide, mini)
            if cfg.get("voice.push_to_talk_key"):
                AppHelper.callAfter(menubar.install_push_to_talk, ptt)
        except Exception as exc:
            log.info("menu bar skipped: %s", exc)

    webview.start(after_start, private_mode=False)
    os._exit(0)
