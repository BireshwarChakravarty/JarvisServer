"""Configuration loading: packaged defaults ⟵ ~/.jarvis/config.yaml ⟵ env vars."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PACKAGE_DIR / "config" / "default.yaml"


def jarvis_home() -> Path:
    """Directory for user data (config, memory db, face prints, logs)."""
    home = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return home


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
    def snapshots_dir(self) -> Path:
        d = self.home / "snapshots"
        d.mkdir(exist_ok=True)
        return d


def _load_env(home: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    for candidate in (home / ".env", Path.cwd() / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> Config:
    home = jarvis_home()
    _load_env(home)
    with open(DEFAULT_CONFIG, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    user_path = Path(path).expanduser() if path else home / "config.yaml"
    source = None
    if user_path.exists():
        with open(user_path, "r", encoding="utf-8") as fh:
            data = deep_merge(data, yaml.safe_load(fh) or {})
        source = user_path

    # A couple of handy env overrides
    if os.environ.get("JARVIS_MODEL"):
        data["brain"]["model"] = os.environ["JARVIS_MODEL"]
    if os.environ.get("JARVIS_OWNER"):
        data["owner"]["name"] = os.environ["JARVIS_OWNER"]
    if overrides:
        data = deep_merge(data, overrides)
    return Config(data=Section(data), home=home, source=source)
