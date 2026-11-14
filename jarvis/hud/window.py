"""Native HUD window (pywebview / WKWebView) with a global show/hide hotkey."""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.window")


def available() -> bool:
    try:
        import webview  # noqa: F401
        return True
    except Exception:
        return False


def run_window(url: str, cfg) -> None:
    """Blocks the main thread until the window is closed."""
    import webview

    window = webview.create_window(
        "J.A.R.V.I.S.", url,
        width=1480, height=920, min_size=(900, 600),
        fullscreen=bool(cfg.get("hud.fullscreen", False)),
        on_top=bool(cfg.get("hud.always_on_top", False)),
        background_color="#02060d",
        text_select=False,
    )
    hotkey = cfg.get("hud.toggle_hotkey")
    if hotkey:
        try:
            from pynput import keyboard

            state = {"shown": True}

            def toggle():
                state["shown"] = not state["shown"]
                (window.show if state["shown"] else window.hide)()

            listener = keyboard.GlobalHotKeys({hotkey: toggle})
            listener.daemon = True
            listener.start()
        except Exception as exc:
            log.info("HUD hotkey unavailable: %s", exc)
    webview.start(private_mode=False)
