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
    assert themes.normalize("light") == "glass"
    assert themes.normalize("iron man") == "stark"
    assert themes.normalize("red and gold") == "stark"
    assert themes.normalize("classic") == "arc"
    assert themes.normalize("banana") is None and themes.normalize("") is None


@pytest.mark.parametrize("said,want", [
    ("switch to the dot matrix theme", "dot"), ("use the liquid glass theme", "glass"), ("stark theme", "stark"),
    ("make it light theme", "glass"), ("go back to the classic look", "arc"), ("dot matrix theme please", "dot"),
])
def test_voice_command(said, want):
    q = quick.match(said)
    assert q is not None and q.tool == "set_theme" and q.args == {"theme": want}


@pytest.mark.parametrize("said", ["switch to dark mode", "party mode", "focus mode", "the banana theme"])
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
    assert asyncio.run(builtin.set_theme(ctx, "glass")) == "Switched to Liquid Glass."
    assert "Liquid Glass (current)" in asyncio.run(builtin.set_theme(ctx, "list"))
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
        text = "\n".join(sheets) + "\n" + "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
        defined = set(re.findall(r"(--[\w-]+)\s*:", text)) | from_js
        used = set(re.findall(r"var\((--[\w-]+)", text))
        missing = sorted(u for u in used if u not in defined)
        assert not missing, (page, missing)


def test_each_theme_overrides_the_core_palette():
    css = (STATIC / "theme.css").read_text()
    for key in ("dot", "glass", "stark"):
        block = re.search(rf':root\[data-theme="{key}"\]\s*\{{(.*?)\}}', css, re.S).group(1)
        for token in ("--ink", "--ice", "--gold", "--text", "--ice-rgb", "--gold-rgb", "--fg-rgb", "--ink-rgb"):
            assert re.search(rf"{token}\s*:", block), (key, token)
    js = (STATIC / "reactor.js").read_text()
    for key in themes.THEMES:
        assert re.search(rf"\b{key}: \{{ mode:", js), key


def test_hello_and_setup_state_carry_the_theme():
    src = (Path(themes.__file__).parents[1] / "hud" / "server.py").read_text()
    assert '"theme": themes.current(cfg)' in src
    assert '"theme"' in (Path(themes.__file__).parents[1] / "remote" / "server.py").read_text().split("FORWARD")[1][:300]
