"""Nightly self-check: quiet when all is well, one clear line (with the fix) when something isn't."""
import datetime as dt
from types import SimpleNamespace

from jarvis.core import health, quick


def app(tmp_path, **caps):
    cfg = SimpleNamespace(home=tmp_path, get=lambda k, d=None: {"voice.enabled": True}.get(k, d))
    return SimpleNamespace(cfg=cfg, capabilities={"microphone": True, "tts": "kokoro-onnx", "stt": "mlx",
                                                  "wake_word": True, **caps},
                           mic=SimpleNamespace(muted=False))


def test_all_fine_says_so(tmp_path, monkeypatch):
    from jarvis.mac import permissions

    monkeypatch.setattr(permissions, "all_status", lambda: [])
    res = health.check(app(tmp_path))
    assert not health.problems(res) and health.summary(res).startswith("All systems are fine")


def test_a_problem_comes_with_its_fix(tmp_path, monkeypatch):
    from jarvis.mac import permissions

    monkeypatch.setattr(permissions, "all_status", lambda: [
        {"key": "microphone", "title": "Microphone", "status": "denied"}])
    a = app(tmp_path, tts="silent")
    res = health.check(a)
    bad = [r["what"] for r in health.problems(res)]
    assert bad == ["voice", "Microphone permission"]
    line = health.summary(res)
    assert line.startswith("One thing needs you: voice") and "Also worth a look: Microphone permission" in line


def test_recent_errors_only_counts_the_last_day(tmp_path):
    now = dt.datetime.now()
    old = (now - dt.timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    new = now.strftime("%Y-%m-%d %H:%M:%S")
    (tmp_path / "jarvis.log").write_text(
        f"{old} ERROR   jarvis: old\n{new} ERROR   jarvis: new\n{new} INFO    jarvis: fine\n{new} ERROR   jarvis: x\n")
    assert health.recent_errors(tmp_path / "jarvis.log") == 2


def test_asking_by_voice():
    for t in ("run diagnostics", "are you all right", "is everything working", "system check"):
        q = quick.match(t)
        assert q is not None and q.tool == "diagnostics", t
