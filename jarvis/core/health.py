"""JARVIS checks himself: nightly, and whenever you ask ("run diagnostics", "are you all right?").

Microphone, voice, hearing, the wake word, his brain, the Mac permissions he relies on, disk space and
recent errors. Nothing is said unless something needs you: then one line in the morning briefing (or
the answer to your question), with what to do about it.
"""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger("jarvis.health")

# permissions whose loss quietly breaks something you'd notice
IMPORTANT_PERMISSIONS = {"microphone": "hearing you", "accessibility": "hand control and 'do it for me'",
                         "automation": "controlling your apps", "full_disk": "WhatsApp and mail"}


def check(j) -> list[dict]:
    """[{what, ok, detail, fix}] for each part of JARVIS."""
    caps = getattr(j, "capabilities", {}) or {}
    cfg = j.cfg
    out: list[dict] = []

    def add(what: str, ok: bool, detail: str = "", fix: str = "") -> None:
        out.append({"what": what, "ok": bool(ok), "detail": detail, "fix": fix})

    voice_on = bool(cfg.get("voice.enabled", True))
    if voice_on:
        mic = getattr(j, "mic", None)
        add("microphone", mic is not None and caps.get("microphone", True),
            "" if mic is not None else "no microphone is open", "check the microphone in Settings › Voice")
        if mic is not None and getattr(mic, "muted", False):
            add("microphone muted", False, "the microphone is muted", "say or click Unmute")
        tts = caps.get("tts", "")
        add("voice", tts not in ("", "silent"), "no voice is available" if tts in ("", "silent") else tts,
            "the voice model may still be downloading; Settings › Voice shows its progress")
        add("hearing", bool(caps.get("stt", True)), "speech recognition isn't loaded",
            "the speech model may still be downloading")
        add("wake word", bool(caps.get("wake_word", True)), "'Hey Jarvis' detection isn't loaded",
            "the wake-word model may still be downloading")
    failures = [t for t in getattr(j, "_turn_failures", []) if time.time() - t < 86400]
    add("brain", len(failures) < 3, f"{len(failures)} requests failed in the last day",
        "check Claude Code is signed in (Settings › Brain › Test)")
    try:
        from jarvis.mac import permissions

        for p in permissions.all_status():
            if p["key"] in IMPORTANT_PERMISSIONS and p["status"] in ("denied", "not_determined"):
                add(f"{p['title']} permission", False, f"needed for {IMPORTANT_PERMISSIONS[p['key']]}",
                    "grant it in Settings › Permissions")
    except Exception as exc:
        log.debug("permissions check failed: %s", exc)
    try:
        free = shutil.disk_usage(Path(cfg.home)).free / 1e9
        add("disk space", free >= 5, f"{free:.0f} GB free", "free some space; models and logs need room")
    except Exception:
        pass
    errors = recent_errors(Path(cfg.home) / "jarvis.log")
    add("error log", errors < 25, f"{errors} errors logged in the last day",
        "say 'send me the log' or look at jarvis.log")
    return out


def recent_errors(path: Path, hours: float = 24.0) -> int:
    """ERROR lines in the log from the last `hours` (the log is rotated, so this stays cheap)."""
    try:
        lines = path.read_text(errors="ignore").splitlines()[-20000:]
    except OSError:
        return 0
    since = dt.datetime.now() - dt.timedelta(hours=hours)
    n = 0
    for line in lines:
        if " ERROR " not in line[:40]:
            continue
        try:
            when = dt.datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if when >= since:
            n += 1
    return n


def problems(results: list[dict]) -> list[dict]:
    return [r for r in results if not r["ok"]]


def summary(results: list[dict]) -> str:
    """One or two spoken sentences."""
    bad = problems(results)
    if not bad:
        return "All systems are fine: hearing, voice, brain, permissions and disk."
    first = bad[0]
    line = f"One thing needs you: {first['what']}, {first['detail']}. To fix it, {first['fix']}."
    if len(bad) > 1:
        rest = ", ".join(b["what"] for b in bad[1:4])
        line += f" Also worth a look: {rest}."
    return line
