"""Release numbers: MAJOR.MINOR from VERSION, then commits since VERSION changed."""
import re
import subprocess
from pathlib import Path

import jarvis

ROOT = Path(__file__).resolve().parents[1]


def test_release_number_shape():
    out = subprocess.run([str(ROOT / "scripts" / "version.sh")], capture_output=True, text=True, check=True).stdout
    assert re.fullmatch(r"\d+\.\d+\.\d+\n", out), out
    assert out.startswith((ROOT / "VERSION").read_text().strip() + ".")


def test_the_app_reports_its_stamped_version(tmp_path, monkeypatch):
    assert re.fullmatch(r"\d+\.\d+\.\d+", jarvis.version())          # source checkout: the package version
    monkeypatch.setattr(jarvis, "_stamp", lambda name: {"_version.txt": "2.0.7", "_build.txt": "abc1234"}[name])
    assert jarvis.version() == "2.0.7" and jarvis.build() == "abc1234"


def test_package_and_app_versions_agree():
    text = (ROOT / "pyproject.toml").read_text()
    versions = re.findall(r'^version = "([\d.]+)"', text, re.MULTILINE)
    assert len(versions) == 2 and versions[0] == versions[1] == jarvis.__version__
    assert jarvis.__version__.startswith((ROOT / "VERSION").read_text().strip() + ".")
