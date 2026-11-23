"""J.A.R.V.I.S. — Just A Rather Very Intelligent System, for macOS."""
from pathlib import Path

__version__ = "2.0.0"


def _stamp(name: str) -> str:
    try:
        return Path(__file__).with_name(name).read_text().strip()
    except OSError:
        return ""


def version() -> str:
    """The release number (2.0.3), written into the app by the Mac build; the source version otherwise."""
    return _stamp("_version.txt")[:20] or __version__


def build() -> str:
    """The commit this copy was built from (written into the app by the Mac build), or "dev"."""
    return _stamp("_build.txt")[:12] or "dev"
