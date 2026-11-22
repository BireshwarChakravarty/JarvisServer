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
    "glass": {"name": "Liquid Glass", "blurb": "Light and airy. Frosted layers over a soft aurora."},
    "stark": {"name": "Stark", "blurb": "Armour crimson and brushed gold on near-black."},
}
DEFAULT = "arc"
# the window's own colour while the page loads, so opening JARVIS never flashes the wrong one
BACKGROUND = {"arc": "#030508", "dot": "#000000", "glass": "#eef1f8", "stark": "#070203"}

_ALIASES = [
    ("dot", r"dot[\s-]*matrix|dots?|led|matrix|retro|pixel|red,? white and black|black,? white and red|mono(chrome)?"),
    ("glass", r"liquid[\s-]*glass|glass|light|white|bright|day|frost(ed)?|aurora|apple"),
    ("stark", r"stark|iron[\s-]*man|mark|armou?r|crimson|red and gold|gold and red|hot[\s-]*rod|red"),
    ("arc", r"arc([\s-]*reactor)?|reactor|classic|original|default|blue|normal|standard|dark"),
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
