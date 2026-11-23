"""Looks. Four themes restyle every JARVIS surface at once: the HUD, the core, the mini capsule,
setup and Settings, the iPhone/iPad companion and the on-screen guidance ring.

The look itself lives in jarvis/hud/static/theme.css and reactor.js; this module names the themes,
understands how people ask for them ("go dark red", "the dot matrix one") and saves the choice
(app.theme), then tells every open window and paired phone to switch.
"""
from __future__ import annotations

import re

THEMES: dict[str, dict[str, str]] = {
    "arc": {"name": "Arc Reactor", "blurb": "Ice-blue holographic light on deep black. The original."},
    "dot": {"name": "Dot Matrix", "blurb": "Black, white and signal red. LED dot type, a dot-grid core."},
    "glass": {"name": "Midnight Glass", "blurb": "Frosted, translucent dark glass over a deep aurora."},
    "stark": {"name": "Stark", "blurb": "Armour crimson and brushed gold on near-black."},
}
DEFAULT = "arc"
# the window's own colour while the page loads, so opening JARVIS never flashes the wrong one
BACKGROUND = {"arc": "#030508", "dot": "#000000", "glass": "#06070d", "stark": "#070203"}

_ALIASES = [
    ("dot", r"dot[\s-]*matrix|dots?|led|matrix|retro|pixel|red,? white and black|black,? white and red|mono(chrome)?"),
    ("glass", r"midnight([\s-]*glass)?|liquid[\s-]*glass|glass|frost(ed)?|aurora|apple"),
    ("stark", r"stark|iron[\s-]*man|mark|armou?r|crimson|red and gold|gold and red|hot[\s-]*rod|red"),
    ("arc", r"arc([\s-]*reactor)?|reactor|classic|original|default|blue|normal|standard"),
]


def normalize(name: str | None) -> str | None:
    """'the dot matrix one' -> 'dot'. None if it isn't recognisably one of the four."""
    text = (name or "").strip().lower()
    if not text:
        return None
    if text in THEMES:
        return text
    for key, pattern in _ALIASES:
        if re.search(rf"\b(?:{pattern})\b", text):
            return key
    return None


def current(cfg) -> str:
    t = str(cfg.get("app.theme", DEFAULT) or DEFAULT).lower() if cfg is not None else DEFAULT
    return t if t in THEMES else DEFAULT


def listing() -> list[dict[str, str]]:
    return [{"id": k, **v} for k, v in THEMES.items()]


def apply(cfg, name: str, bus=None) -> str:
    """Save the theme and switch every window and paired device. Returns the theme id."""
    key = normalize(name)
    if key is None:
        raise ValueError(f"unknown theme {name!r}")
    try:
        from jarvis.app.setup_api import _write_user_config

        _write_user_config(cfg, {"app": {"theme": key}})
    except Exception:
        app = cfg.data.setdefault("app", {}) if hasattr(cfg, "data") else None
        if isinstance(app, dict):
            app["theme"] = key
    if bus is None:
        from jarvis.events import bus as _bus

        bus = _bus
    bus.emit("theme", theme=key, name=THEMES[key]["name"])
    return key


# ── clock: 24-hour or 12-hour, everywhere a time is shown ─────────────────────────────────────────
# app.clock_24h: true (13:05, the default), false (1:05 PM) or null to follow the Mac's own setting
# (System Settings › General › Date & Time › 24-hour time). If the Mac can't be asked, 24-hour.
def mac_uses_24h() -> bool | None:
    """The Mac's own choice (System Settings › General › Date & Time › 24-hour time), or None if it
    can't be told. macOS stores an explicit choice as AppleICUForce24HourTime / AppleICUForce12HourTime;
    without one, the region's time format decides. Asked fresh each time (it's cheap), so a change in
    System Settings shows up without restarting JARVIS."""
    try:
        from jarvis.mac.system import IS_MAC

        if not IS_MAC:
            return None
        import subprocess

        def flag(key: str) -> bool:
            out = subprocess.run(["defaults", "read", "-g", key], capture_output=True, text=True, timeout=3)
            return out.returncode == 0 and out.stdout.strip().lower() in ("1", "true", "yes")
        if flag("AppleICUForce24HourTime"):
            return True
        if flag("AppleICUForce12HourTime"):
            return False
        from AppKit import NSDateFormatter, NSLocale

        fmt = str(NSDateFormatter.dateFormatFromTemplate_options_locale_("j", 0, NSLocale.autoupdatingCurrentLocale())
                  or "")
        return ("H" in fmt or "k" in fmt) if fmt else None
    except Exception:
        return None


def clock_24h(cfg) -> bool:
    """True (24-hour) or False (12-hour), for every page. Never left to the browser's language."""
    v = cfg.get("app.clock_24h", True) if cfg is not None else True
    if isinstance(v, bool):
        return v
    mac = mac_uses_24h()
    return True if mac is None else mac


def clock_setting(cfg) -> str:
    v = cfg.get("app.clock_24h", True) if cfg is not None else True
    return "24" if v is True else "12" if v is False else "auto"


def set_clock(cfg, mode: str, bus=None) -> bool:
    """mode: '24', '12' or 'auto' (follow the Mac). Saves it and updates every window and phone."""
    value = {"24": True, "12": False, "auto": None}[mode]
    try:
        from jarvis.app.setup_api import _write_user_config

        _write_user_config(cfg, {"app": {"clock_24h": value}})
    except Exception:
        if hasattr(cfg, "data"):
            cfg.data.setdefault("app", {})["clock_24h"] = value
    if bus is None:
        from jarvis.events import bus as _bus

        bus = _bus
    shown = clock_24h(cfg)
    bus.emit("prefs", clock24=shown)
    return shown
