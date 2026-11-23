"""Themes: four looks that restyle every surface at once, chosen in Settings or by voice."""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.core import quick, themes

STATIC = Path(__file__).resolve().parents[1] / "jarvis" / "hud" / "static"
PAGES = ["index", "mini", "onboarding", "pair", "remote", "guide"]


def test_names_and_descriptions_resolve():
    assert themes.normalize("dot") == "dot"
    assert themes.normalize("the dot matrix one") == "dot"
    assert themes.normalize("red, white and black") == "dot"
    assert themes.normalize("liquid glass") == "glass"
    assert themes.normalize("midnight glass") == "glass"
    assert themes.normalize("light") is None and themes.normalize("white") is None    # no light themes
    assert themes.normalize("iron man") == "stark"
    assert themes.normalize("red and gold") == "stark"
    assert themes.normalize("classic") == "arc"
    assert themes.normalize("banana") is None and themes.normalize("") is None


@pytest.mark.parametrize("said,want", [
    ("switch to the dot matrix theme", "dot"), ("use the liquid glass theme", "glass"), ("stark theme", "stark"),
    ("switch to midnight glass theme", "glass"), ("go back to the classic look", "arc"), ("dot matrix theme please", "dot"),
])
def test_voice_command(said, want):
    q = quick.match(said)
    assert q is not None and q.tool == "set_theme" and q.args == {"theme": want}


@pytest.mark.parametrize("said", ["switch to dark mode", "party mode", "focus mode", "the banana theme", "light theme"])
def test_other_modes_are_left_alone(said):
    q = quick.match(said)
    assert q is None or q.tool != "set_theme"


class Cfg:
    def __init__(self, tmp):
        self.data = {"app": {"theme": "arc"}}
        self.user_config_path = Path(tmp) / "config.yaml"

    def get(self, key, default=None):
        cur = self.data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def test_apply_saves_and_tells_every_window(tmp_path):
    cfg, seen = Cfg(tmp_path), []
    bus = SimpleNamespace(emit=lambda kind, **k: seen.append((kind, k)))
    assert themes.apply(cfg, "dot matrix", bus=bus) == "dot"
    assert themes.current(cfg) == "dot"
    assert "theme: dot" in cfg.user_config_path.read_text()
    assert seen == [("theme", {"theme": "dot", "name": "Dot Matrix"})]
    with pytest.raises(ValueError):
        themes.apply(cfg, "banana", bus=bus)
    cfg.data["app"]["theme"] = "nonsense"
    assert themes.current(cfg) == "arc"                     # a bad value never breaks the look


def test_the_tool(tmp_path, monkeypatch):
    from jarvis.tools import builtin

    cfg = Cfg(tmp_path)
    monkeypatch.setattr(themes, "apply", lambda c, k, bus=None: c.data["app"].__setitem__("theme", k) or k)
    ctx = SimpleNamespace(cfg=cfg)
    assert asyncio.run(builtin.set_theme(ctx, "glass")) == "Switched to Midnight Glass."
    assert "Midnight Glass (current)" in asyncio.run(builtin.set_theme(ctx, "list"))
    assert "don't have a theme" in asyncio.run(builtin.set_theme(ctx, "banana"))


def test_every_page_loads_the_theme_first():
    for page in PAGES:
        html = (STATIC / f"{page}.html").read_text()
        assert "theme.js" in html and "theme.css" in html, page
        # theme.css comes after the page's own stylesheet so its overrides win
        sheets = re.findall(r'href="[^"]*/([\w-]+\.css)"', html)
        assert sheets[-1] == "theme.css", (page, sheets)


def test_every_colour_token_used_is_defined():
    """The pages colour themselves from tokens; a token a page's stylesheets never define silently turns a
    colour off. Checked per page, against exactly the stylesheets that page loads (plus tokens its script sets)."""
    from_js = set(re.findall(r"setProperty\(\s*[\"'`](--[\w-]+)", "\n".join(p.read_text() for p in STATIC.glob("*.js"))))
    for page in PAGES:
        html = (STATIC / f"{page}.html").read_text()
        sheets = [(STATIC / name).read_text() for name in re.findall(r'href="[^"]*/([\w-]+\.css)"', html)]
        text = "\n".join(sheets) + "\n" + "\n".join(re.findall(r"<style>(.*?)</style>", html, re.DOTALL))
        defined = set(re.findall(r"(--[\w-]+)\s*:", text)) | from_js
        used = set(re.findall(r"var\((--[\w-]+)", text))
        missing = sorted(u for u in used if u not in defined)
        assert not missing, (page, missing)


def test_each_theme_overrides_the_core_palette():
    css = (STATIC / "theme.css").read_text()
    for key in ("dot", "glass", "stark"):
        block = re.search(rf':root\[data-theme="{key}"\]\s*\{{(.*?)\}}', css, re.DOTALL).group(1)
        for token in ("--ink", "--ice", "--gold", "--text", "--ice-rgb", "--gold-rgb", "--fg-rgb", "--ink-rgb"):
            assert re.search(rf"{token}\s*:", block), (key, token)
    js = (STATIC / "reactor.js").read_text()
    for key in themes.THEMES:
        assert re.search(rf"\b{key}: \{{ mode:", js), key


def test_hello_and_setup_state_carry_the_theme():
    src = (Path(themes.__file__).parents[1] / "hud" / "server.py").read_text()
    assert '"theme": themes.current(cfg)' in src
    assert '"theme"' in (Path(themes.__file__).parents[1] / "remote" / "server.py").read_text().split("FORWARD")[1][:300]


def test_every_theme_is_dark():
    """No light themes: each theme's page colour is near-black."""
    css = (STATIC / "theme.css").read_text()
    for key, bg in themes.BACKGROUND.items():
        r, g, b = (int(bg[i:i + 2], 16) for i in (1, 3, 5))
        assert max(r, g, b) < 0x20, key
        if key != "arc":
            ink = re.search(rf':root\[data-theme="{key}"\]\s*\{{.*?--ink:\s*(#[0-9a-f]+)', css, re.DOTALL).group(1)
            assert ink.lower().startswith("#0") or ink == "#000", (key, ink)
    assert "color-scheme: light" not in css


def test_the_mini_capsule_and_phone_follow_the_theme():
    """The capsule (and the phone) switch on the server's hello/theme events and on another window's change."""
    for page in ("mini.js", "hud.js", "remote.js"):
        js = (STATIC / page).read_text()
        assert '(e.type === "hello" || e.type === "theme") && e.theme' in js, page
    assert 'addEventListener("storage"' in (STATIC / "theme.js").read_text()
    css = (STATIC / "theme.css").read_text()
    assert ':root[data-theme="glass"] #pill' in css                 # smoked, never see-through on a bright wallpaper


def test_pages_always_load_the_matching_scripts():
    """After an update the window must fetch the new scripts, never run old cached ones (an old core
    inside a new page was how a theme reached the page but not the core)."""
    from fastapi.testclient import TestClient

    from jarvis.hud import assets
    from jarvis.hud.server import create_app

    client = TestClient(create_app(None))
    html = client.get("/mini").text
    assert re.search(r'/static/reactor\.js\?v=[0-9a-f]{10}"', html)
    assert re.search(r'/static/theme\.css\?v=[0-9a-f]{10}"', html)
    assert "fonts.googleapis.com/css2?family=Geist" in html                  # outside links untouched
    r = client.get("/static/reactor.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    # the fingerprint follows the file's contents
    assert assets.fingerprint(STATIC / "reactor.js") in html
    assert assets.versioned('<script src="/remote/static/remote.js"></script>', STATIC).count("?v=") == 1


@pytest.mark.parametrize("said,mode", [("use 24 hour time", "24"), ("switch to a 12 hour clock", "12"),
                                       ("24-hour clock", "24"), ("use twenty four hour time", "24")])
def test_clock_by_voice(said, mode):
    q = quick.match(said)
    assert q is not None and q.tool == "clock_format" and q.args == {"mode": mode}


@pytest.mark.parametrize("said", ["24 hours", "set a 24 hour timer", "for 12 hours"])
def test_clock_leaves_durations_alone(said):
    q = quick.match(said)
    assert q is None or q.tool != "clock_format"


def test_clock_setting_saves_and_tells_every_window(tmp_path, monkeypatch):
    cfg, seen = Cfg(tmp_path), []
    bus = SimpleNamespace(emit=lambda kind, **k: seen.append((kind, k)))
    monkeypatch.setattr(themes, "mac_uses_24h", lambda: False)
    assert themes.clock_setting(cfg) == "24" and themes.clock_24h(cfg) is True        # 24-hour by default
    assert themes.set_clock(cfg, "12", bus=bus) is False
    assert themes.clock_setting(cfg) == "12" and "clock_24h: false" in cfg.user_config_path.read_text()
    assert seen[-1] == ("prefs", {"clock24": False})
    themes.set_clock(cfg, "auto", bus=bus)                                            # follow the Mac
    assert themes.clock_24h(cfg) is False and seen[-1] == ("prefs", {"clock24": False})
    monkeypatch.setattr(themes, "mac_uses_24h", lambda: None)                          # can't ask the Mac
    assert themes.clock_24h(cfg) is True


def test_the_page_clock_is_24_hour_unless_told_otherwise():
    js = (STATIC / "theme.js").read_text()
    assert 'return v !== "0"; })();' in js                    # nothing stored yet: 24-hour
    assert 'const cycle = () => (h24 ? { hourCycle: "h23" } : { hour12: true });' in js   # never the language default
    from jarvis.config import load_config

    assert load_config().get("app.clock_24h") is True


def test_every_shown_time_goes_through_the_clock_setting():
    for page in ("hud.js", "mini.js", "remote.js", "reactor.js"):
        js = (STATIC / page).read_text()
        bare = re.findall(r'toLocaleTimeString\(\[\], \{ hour: "numeric"', js)
        assert len(bare) <= (1 if page == "reactor.js" else 0), page          # reactor keeps a fallback only
        assert "JarvisClock" in js, page
    for page in ("hud.js", "mini.js", "remote.js"):
        assert '(e.type === "hello" || e.type === "prefs") && "clock24" in e' in (STATIC / page).read_text(), page
