"""Thin, dependency-free wrappers around macOS automation: osascript (AppleScript/JXA),
`open`, `shortcuts`, `pmset`, `screencapture`, `mdfind` … Everything degrades gracefully
on other platforms so the rest of JARVIS can be developed and tested anywhere."""
from __future__ import annotations

import json
import logging
import platform
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("jarvis.mac")

IS_MAC = platform.system() == "Darwin"


class MacOnly(RuntimeError):
    pass


def require_mac() -> None:
    if not IS_MAC:
        raise MacOnly("This capability needs macOS.")


def run(cmd: list[str] | str, timeout: float = 20, check: bool = False, input_text: str | None = None) -> str:
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, input=input_text
    )
    if check and proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"exit {proc.returncode}").strip())
    return (proc.stdout or "").strip()


def osascript(script: str, timeout: float = 20, language: str = "AppleScript") -> str:
    """Run AppleScript (or JavaScript for Automation) and return stdout."""
    require_mac()
    cmd = ["osascript", "-l", language, "-"]
    proc = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "osascript failed")
    return proc.stdout.strip()


def q(text: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


# ── apps ──────────────────────────────────────────────────────────────────
def open_app(name: str) -> str:
    require_mac()
    proc = subprocess.run(["open", "-a", name], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Couldn't find an application called {name!r}.")
    return f"Opened {name}."


def quit_app(name: str) -> str:
    osascript(f"tell application {q(name)} to quit")
    return f"Closed {name}."


def running_apps() -> list[str]:
    out = osascript(
        'tell application "System Events" to get name of every process whose background only is false'
    )
    return [a.strip() for a in out.split(",") if a.strip()]


def frontmost_app() -> str:
    try:
        return osascript(
            'tell application "System Events" to get name of first application process whose frontmost is true'
        )
    except Exception:
        return ""


def open_target(target: str) -> str:
    require_mac()
    run(["open", target], check=True)
    return f"Opened {target}."


# ── audio / display ───────────────────────────────────────────────────────
def set_volume(level: int) -> str:
    level = max(0, min(100, int(level)))
    osascript(f"set volume output volume {level}")
    return f"Volume set to {level}%."


def get_volume() -> int:
    return int(osascript("output volume of (get volume settings)") or 0)


def set_mute(muted: bool) -> str:
    osascript(f"set volume output muted {'true' if muted else 'false'}")
    return "Muted." if muted else "Unmuted."


def set_brightness(level: int) -> str:
    """Uses the `brightness` CLI if present (brew install brightness), else key presses."""
    require_mac()
    level = max(0, min(100, int(level)))
    if shutil.which("brightness"):
        run(["brightness", f"{level / 100:.2f}"], check=True)
        return f"Brightness set to {level}%."
    # Fallback: 16 steps via the brightness keys (key codes 145 down / 144 up)
    steps = round(level / 100 * 16)
    script = (
        'tell application "System Events"\n'
        "repeat 16 times\nkey code 145\nend repeat\n"
        f"repeat {steps} times\nkey code 144\nend repeat\n"
        "end tell"
    )
    osascript(script)
    return f"Brightness set to roughly {level}%."


def toggle_dark_mode(enable: bool | None = None) -> str:
    value = "not dark mode" if enable is None else ("true" if enable else "false")
    osascript(
        'tell application "System Events" to tell appearance preferences to '
        f"set dark mode to {value}"
    )
    return "Appearance updated."


# ── system power / session ────────────────────────────────────────────────
def lock_screen() -> str:
    require_mac()
    # Ctrl+Cmd+Q is the system lock shortcut
    osascript('tell application "System Events" to keystroke "q" using {control down, command down}')
    return "Screen locked."


def sleep_display() -> str:
    require_mac()
    run(["pmset", "displaysleepnow"])
    return "Display sleeping."


def sleep_system() -> str:
    osascript('tell application "System Events" to sleep')
    return "Going to sleep."


def notify(title: str, message: str, sound: str | None = "Glass") -> str:
    script = f"display notification {q(message)} with title {q(title)}"
    if sound:
        script += f" sound name {q(sound)}"
    osascript(script)
    return "Notification shown."


def battery() -> dict:
    if IS_MAC:
        out = run(["pmset", "-g", "batt"])
        info: dict = {"raw": out}
        import re

        m = re.search(r"(\d+)%;\s*([\w ]+);\s*([^\n;]*)", out)
        if m:
            info.update(percent=int(m.group(1)), state=m.group(2).strip(), remaining=m.group(3).strip())
        info["source"] = "AC" if "AC Power" in out else "Battery"
        return info
    try:
        import psutil

        b = psutil.sensors_battery()
        if b:
            return {"percent": int(b.percent), "state": "charging" if b.power_plugged else "discharging"}
    except Exception:
        pass
    return {}


# ── clipboard / typing ────────────────────────────────────────────────────
def get_clipboard() -> str:
    require_mac()
    return run(["pbpaste"])


def set_clipboard(text: str) -> str:
    require_mac()
    subprocess.run(["pbcopy"], input=text, text=True, check=True)
    return "Copied to clipboard."


def type_text(text: str) -> str:
    osascript(f'tell application "System Events" to keystroke {q(text)}')
    return "Typed."


# ── Shortcuts (gateway to HomeKit, Focus modes, and every app) ─────────────
def list_shortcuts() -> list[str]:
    require_mac()
    return [s for s in run(["shortcuts", "list"]).splitlines() if s.strip()]


def run_shortcut(name: str, text_input: str | None = None) -> str:
    require_mac()
    cmd = ["shortcuts", "run", name]
    out_file = Path(tempfile.mkstemp(suffix=".txt")[1])
    cmd += ["--output-path", str(out_file)]
    if text_input:
        in_file = Path(tempfile.mkstemp(suffix=".txt")[1])
        in_file.write_text(text_input)
        cmd += ["--input-path", str(in_file)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"Shortcut {name!r} failed")
    try:
        result = out_file.read_text(errors="ignore").strip()
    except Exception:
        result = ""
    return result or f"Shortcut {name!r} completed."


# ── files ────────────────────────────────────────────────────────────────
def spotlight(query: str, limit: int = 15, kind: str | None = None) -> list[str]:
    require_mac()
    expr = query
    if kind:
        expr = f"kMDItemKind == '*{kind}*'c && kMDItemDisplayName == '*{query}*'c"
    out = run(["mdfind", "-name", query] if not kind else ["mdfind", expr], timeout=15)
    return out.splitlines()[:limit]


def screenshot(path: Path | None = None) -> Path:
    require_mac()
    path = path or Path(tempfile.mkstemp(suffix=".png")[1])
    run(["screencapture", "-x", "-C", str(path)], check=True)
    return path


def wifi_name() -> str:
    if not IS_MAC:
        return ""
    try:
        out = run(["networksetup", "-getairportnetwork", "en0"])
        return out.split(": ", 1)[1] if ": " in out else ""
    except Exception:
        return ""


def system_profile() -> dict:
    info = {"os": platform.platform(), "machine": platform.machine()}
    if IS_MAC:
        try:
            info["model"] = run(["sysctl", "-n", "hw.model"])
            info["chip"] = run(["sysctl", "-n", "machdep.cpu.brand_string"])
            info["macos"] = run(["sw_vers", "-productVersion"])
        except Exception:
            pass
    return info


def jxa(script: str) -> object:
    """Run JavaScript for Automation and parse a JSON result."""
    out = osascript(script, language="JavaScript")
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out
