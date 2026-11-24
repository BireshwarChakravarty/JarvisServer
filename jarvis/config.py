"""Configuration loading: packaged defaults ⟵ ~/.jarvis/config.yaml ⟵ env vars."""
from __future__ import annotations

import copy
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PACKAGE_DIR / "config" / "default.yaml"


def jarvis_home() -> Path:
    """Directory for user data (config, memory db, face prints, models, logs).

    JARVIS_HOME wins; an existing ~/.jarvis is kept for older installs; otherwise the
    proper macOS location ~/Library/Application Support/JARVIS (or ~/.jarvis elsewhere)."""
    if os.environ.get("JARVIS_HOME"):
        home = Path(os.environ["JARVIS_HOME"]).expanduser()
    elif (Path.home() / ".jarvis").exists() or sys.platform != "darwin":
        home = Path.home() / ".jarvis"
    else:
        home = Path.home() / "Library" / "Application Support" / "JARVIS"
    home.mkdir(parents=True, exist_ok=True)
    return home


def running_as_app() -> bool:
    """True inside the packaged JARVIS.app bundle."""
    return ".app/Contents/" in (sys.executable or "") or os.environ.get("JARVIS_APP") == "1"


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Section(dict):
    """dict with attribute access, recursively. cfg.voice.tts.voice"""

    def __getattr__(self, item: str) -> Any:
        try:
            value = self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc
        return Section(value) if isinstance(value, dict) and not isinstance(value, Section) else value

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value


@dataclass
class Config:
    data: Section
    home: Path
    source: Path | None = None
    _sections: dict = field(default_factory=dict, repr=False)

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_") or item in ("data", "home", "source"):
            raise AttributeError(item)
        return getattr(self.data, item)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def db_path(self) -> Path:
        return self.home / "memory.db"

    @property
    def faces_dir(self) -> Path:
        d = self.home / "faces"
        d.mkdir(exist_ok=True)
        return d

    @property
    def models_dir(self) -> Path:
        d = self.home / "models"
        d.mkdir(exist_ok=True)
        return d

    @property
    def protocols_path(self) -> Path:
        return self.home / "protocols.yaml"

    @property
    def setup_complete(self) -> bool:
        return (self.home / ".setup-complete").exists()

    @property
    def user_config_path(self) -> Path:
        return self.home / "config.yaml"

    @property
    def snapshots_dir(self) -> Path:
        d = self.home / "snapshots"
        d.mkdir(exist_ok=True)
        return d


def _load_env(home: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        load_dotenv = None
    if load_dotenv:
        for candidate in (home / ".env", Path.cwd() / ".env"):
            if candidate.exists():
                load_dotenv(candidate, override=False)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from jarvis.mac.keychain import get_api_key

            key = get_api_key()
            if key:
                os.environ["ANTHROPIC_API_KEY"] = key
        except Exception:
            pass


# Voices offered once and since retired → the nearest one that's left.
RETIRED_VOICES = {"george": "jarvis", "fable": "jarvis", "emma": "jarvis", "assistant": "butler"}


def _retire_voices(data: dict) -> None:
    """A voice that's no longer offered (an Apple voice, George, Fable, Emma, Assistant) becomes the
    nearest one that is, so an old config.yaml keeps working."""
    tts = (data.get("voice") or {}).get("tts")
    if not isinstance(tts, dict):
        return
    if tts.get("voice") in RETIRED_VOICES:
        tts["voice"] = RETIRED_VOICES[tts["voice"]]
    if tts.get("backend") == "say":            # Apple voices aren't offered any more: the voices JARVIS ships
        tts["backend"] = "auto"


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> Config:
    home = jarvis_home()
    _load_env(home)
    with open(DEFAULT_CONFIG, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    user_path = Path(path).expanduser() if path else home / "config.yaml"
    source = None
    if user_path.exists():
        try:
            with open(user_path, encoding="utf-8") as fh:
                mine = yaml.safe_load(fh) or {}
            if not isinstance(mine, dict):
                raise yaml.YAMLError("the file isn't a set of settings")
            data = deep_merge(data, mine)
            source = user_path
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            # a typo in config.yaml must not stop JARVIS starting: keep a copy, carry on with defaults
            import logging
            import shutil

            bad = user_path.with_name("config.yaml.broken")
            shutil.copy2(user_path, bad)
            logging.getLogger("jarvis.config").error("couldn't read %s (%s); using defaults. A copy is in %s",
                                                     user_path, exc, bad)

    _retire_voices(data)

    # A couple of handy env overrides
    # The app bundle on macOS uses the bundle-friendly engines (see packaging/)
    if running_as_app():
        os.environ.setdefault("JARVIS_APP", "1")
    if os.environ.get("JARVIS_MODEL"):
        data["brain"]["model"] = os.environ["JARVIS_MODEL"]
    if os.environ.get("JARVIS_OWNER"):
        data["owner"]["name"] = os.environ["JARVIS_OWNER"]
    if overrides:
        data = deep_merge(data, overrides)
    return Config(data=Section(data), home=home, source=source)
