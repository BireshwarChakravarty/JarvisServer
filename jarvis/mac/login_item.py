"""Launch at login. Uses SMAppService (macOS 13+) so JARVIS appears in
System Settings › General › Login Items like any native app."""
from __future__ import annotations

import logging
import sys

log = logging.getLogger("jarvis.login")

STATUS = {0: "off", 1: "on", 2: "needs_approval", 3: "unavailable"}


def _service():
    from ServiceManagement import SMAppService

    return SMAppService.mainAppService()


def status() -> str:
    if sys.platform != "darwin":
        return "unavailable"
    try:
        return STATUS.get(int(_service().status()), "unknown")
    except Exception as exc:
        log.info("SMAppService unavailable: %s", exc)
        return "unavailable"


def set_enabled(enabled: bool) -> str:
    if sys.platform != "darwin":
        return "unavailable"
    try:
        svc = _service()
        if enabled:
            ok, err = svc.registerAndReturnError_(None)
        else:
            ok, err = svc.unregisterAndReturnError_(None)
        if not ok:
            log.warning("login item change failed: %s", err)
        st = status()
        if enabled and st == "needs_approval":
            from ServiceManagement import SMAppService

            SMAppService.openSystemSettingsLoginItems()
        return st
    except Exception as exc:
        log.warning("SMAppService failed (%s); falling back to System Events", exc)
        try:
            from jarvis.mac.system import osascript, q

            app_path = sys.executable.split(".app/Contents/")[0] + ".app"
            if enabled:
                osascript('tell application "System Events" to if not (exists login item "JARVIS") then '
                          f"make login item at end with properties {{path:{q(app_path)}, hidden:false}}")
            else:
                osascript('tell application "System Events" to if exists login item "JARVIS" then '
                          'delete login item "JARVIS"')
            return "on" if enabled else "off"
        except Exception as exc2:
            log.error("login item fallback failed: %s", exc2)
            return "unavailable"
