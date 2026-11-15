import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def jarvis_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jhome"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    yield tmp_path / "jhome"


@pytest.fixture
def cfg(jarvis_home):
    from jarvis.config import load_config

    return load_config(overrides={
        "voice": {"enabled": False}, "vision": {"enabled": False},
        "greeting": {"briefing": False}, "brain": {"web_search": True, "provider": "anthropic"},
    })
