"""Check and request macOS privacy permissions natively (PyObjC), so onboarding can
show live status and trigger the real system prompts instead of sending you hunting
through System Settings.

Status values: "granted" | "denied" | "not_determined" | "restricted" | "unknown" | "unavailable"
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading

log = logging.getLogger("jarvis.permissions")

SETTINGS_PANES = {
    "microphone": "Privacy_Microphone",
    "camera": "Privacy_Camera",
    "calendar": "Privacy_Calendars",
    "reminders": "Privacy_Reminders",
    "contacts": "Privacy_Contacts",
    "automation": "Privacy_Automation",
    "accessibility": "Privacy_Accessibility",
    "screen": "Privacy_ScreenCapture",
}

CATALOG = [
    {"key": "microphone", "title": "Microphone", "why": "Hear “Hey Jarvis” and your voice", "required": True},
    {"key": "camera", "title": "Camera", "why": "Recognise you, read gestures, “what am I holding?”", "required": False},
    {"key": "calendar", "title": "Calendars", "why": "Your agenda, briefings and new events", "required": False},
    {"key": "reminders", "title": "Reminders", "why": "Create and read reminders", "required": False},
    {"key": "contacts", "title": "Contacts", "why": "Message people by name", "required": False},
    {"key": "automation", "title": "App control", "why": "Control Music, Messages and Finder, and read your email", "required": False},
    {"key": "screen", "title": "Screen recording", "why": "“What's on my screen?”", "required": False},
    {"key": "accessibility", "title": "Accessibility", "why": "Hold ⌥ to talk, type text for you", "required": False},
]

IS_MAC = sys.platform == "darwin"


def _wait(register) -> bool:
    """Call register(callback) and block until the system calls back (granted?)."""
    done = threading.Event()
    box = {"ok": False}

    def cb(granted, *rest):
        box["ok"] = bool(granted)
        done.set()

    register(cb)
    done.wait(300)
    return box["ok"]


# ── individual permissions ────────────────────────────────────────────────
def _av(media: str) -> str:
    import AVFoundation as AV

    mt = AV.AVMediaTypeAudio if media == "audio" else AV.AVMediaTypeVideo
    return {0: "not_determined", 1: "restricted", 2: "denied", 3: "granted"}.get(
        int(AV.AVCaptureDevice.authorizationStatusForMediaType_(mt)), "unknown")


def _av_request(media: str) -> bool:
    import AVFoundation as AV

    mt = AV.AVMediaTypeAudio if media == "audio" else AV.AVMediaTypeVideo
    return _wait(lambda cb: AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(mt, cb))


def _ek(entity: str) -> str:
    import EventKit as EK

    et = EK.EKEntityTypeEvent if entity == "event" else EK.EKEntityTypeReminder
    # 0 notDetermined, 1 restricted, 2 denied, 3 fullAccess (authorized), 4 writeOnly
    return {0: "not_determined", 1: "restricted", 2: "denied", 3: "granted", 4: "granted"}.get(
        int(EK.EKEventStore.authorizationStatusForEntityType_(et)), "unknown")


def _ek_request(entity: str) -> bool:
    import EventKit as EK

    store = EK.EKEventStore.alloc().init()
    if entity == "event":
        if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
            return _wait(store.requestFullAccessToEventsWithCompletion_)
        return _wait(lambda cb: store.requestAccessToEntityType_completion_(EK.EKEntityTypeEvent, cb))
    if hasattr(store, "requestFullAccessToRemindersWithCompletion_"):
        return _wait(store.requestFullAccessToRemindersWithCompletion_)
    return _wait(lambda cb: store.requestAccessToEntityType_completion_(EK.EKEntityTypeReminder, cb))


def _contacts() -> str:
    import Contacts as CN

    return {0: "not_determined", 1: "restricted", 2: "denied", 3: "granted", 4: "granted"}.get(
        int(CN.CNContactStore.authorizationStatusForEntityType_(CN.CNEntityTypeContacts)), "unknown")


def _contacts_request() -> bool:
    import Contacts as CN

    store = CN.CNContactStore.alloc().init()
    return _wait(lambda cb: store.requestAccessForEntityType_completionHandler_(CN.CNEntityTypeContacts, cb))


def _accessibility() -> str:
    import ApplicationServices as AS

    return "granted" if AS.AXIsProcessTrusted() else "not_determined"


def _accessibility_request() -> bool:
    import ApplicationServices as AS

    opts = {AS.kAXTrustedCheckOptionPrompt: True}
    trusted = bool(AS.AXIsProcessTrustedWithOptions(opts))
    if not trusted:
        open_settings("accessibility")
    return trusted


def _screen() -> str:
    import Quartz

    return "granted" if Quartz.CGPreflightScreenCaptureAccess() else "not_determined"


def _screen_request() -> bool:
    import Quartz

    return bool(Quartz.CGRequestScreenCaptureAccess())


_automation_cache: dict[str, str] = {}


def _automation_probe(prompt: bool) -> str:
    """Apple Events permission for System Events (the hub for most automation)."""
    if not prompt and "status" in _automation_cache:
        return _automation_cache["status"]
    if not prompt:
        return "unknown"
    proc = subprocess.run(
        ["/usr/bin/osascript", "-e", 'tell application "System Events" to return name of first process'],
        capture_output=True, text=True, timeout=120)
    if proc.returncode == 0:
        status = "granted"
    elif "-1743" in proc.stderr or "not allowed" in proc.stderr.lower():
        status = "denied"
    else:
        status = "unknown"
    # Finder too (trash/reveal) prompts separately. Email comes from Thunderbird's local files: no prompt.
    for app in ("Finder",):
        subprocess.run(["/usr/bin/osascript", "-e", f'tell application "{app}" to return name'],
                       capture_output=True, text=True, timeout=120)
    _automation_cache["status"] = status
    return status


# ── public API ────────────────────────────────────────────────────────────
def status(key: str) -> str:
    if not IS_MAC:
        return "unavailable"
    try:
        return {
            "microphone": lambda: _av("audio"),
            "camera": lambda: _av("video"),
            "calendar": lambda: _ek("event"),
            "reminders": lambda: _ek("reminder"),
            "contacts": _contacts,
            "accessibility": _accessibility,
            "screen": _screen,
            "automation": lambda: _automation_probe(False),
        }[key]()
    except Exception as exc:
        log.warning("permission status %s failed: %s", key, exc)
        return "unknown"


def request(key: str) -> str:
    """Trigger the system prompt (blocks until answered). If already denied, open Settings."""
    if not IS_MAC:
        return "unavailable"
    current = status(key)
    if current == "granted":
        return current
    if current == "denied" and key not in ("automation",):
        open_settings(key)
        return current
    try:
        if key == "microphone":
            _av_request("audio")
        elif key == "camera":
            _av_request("video")
        elif key == "calendar":
            _ek_request("event")
        elif key == "reminders":
            _ek_request("reminder")
        elif key == "contacts":
            _contacts_request()
        elif key == "accessibility":
            _accessibility_request()
        elif key == "screen":
            if not _screen_request():
                open_settings("screen")
        elif key == "automation":
            if _automation_probe(True) == "denied":
                open_settings("automation")
    except Exception as exc:
        log.warning("permission request %s failed: %s", key, exc)
    return status(key)


def all_status() -> list[dict]:
    return [{**p, "status": status(p["key"])} for p in CATALOG]


def open_settings(key: str) -> None:
    pane = SETTINGS_PANES.get(key)
    if not pane or not IS_MAC:
        return
    subprocess.run(["/usr/bin/open", f"x-apple.systempreferences:com.apple.preference.security?{pane}"],
                   capture_output=True)


def signing_kind(bundle) -> str:
    """How the installed app is signed: "local" (this Mac's stable JARVIS identity: permissions
    survive updates), "adhoc" (each update looks like a new app to macOS), or "unknown"."""
    if not IS_MAC or not bundle:
        return "unknown"
    try:
        out = subprocess.run(["/usr/bin/codesign", "-dvv", str(bundle)], capture_output=True, text=True,
                             timeout=20).stderr
    except Exception:
        return "unknown"
    if "Authority=JARVIS Local Code Signing" in out:
        return "local"
    if "Signature=adhoc" in out:
        return "adhoc"
    return "unknown"
