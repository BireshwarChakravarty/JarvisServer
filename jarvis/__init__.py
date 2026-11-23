"""J.A.R.V.I.S. — Just A Rather Very Intelligent System, for macOS."""
__version__ = "1.0.0"


def build() -> str:
    """The commit this copy was built from (written into the app by the Mac build), or "dev"."""
    from pathlib import Path

    try:
        return (Path(__file__).with_name("_build.txt").read_text().strip() or "dev")[:12]
    except OSError:
        return "dev"
