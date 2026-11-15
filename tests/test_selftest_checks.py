"""Run the platform-independent in-app self-test checks here too, so a mistake in a check is
caught by the ordinary test run rather than by the Mac build."""
import textwrap
from pathlib import Path

SRC = (Path(__file__).parent.parent / "jarvis" / "app" / "selftest.py").read_text()


def run_check(title: str, state: dict) -> str:
    start = SRC.index(f'    @check("{title}"')
    body = SRC[start:]
    body = body[body.index("    def _():"):body.index("    @check(", 10)]
    ns = {"state": state}
    exec(textwrap.dedent(body), ns)
    return ns["_"]()


def test_motion_gesture_selftest(cfg):
    assert run_check("eyes: motion gestures (swipe, pinch dial)", {"cfg": cfg}).startswith("swipe → next track")
