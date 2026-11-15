"""One-click update: run the same installer as the one-line install command, in the background.

The installer downloads the latest verified build, swaps it into /Applications, re-signs it with
this Mac's private JARVIS identity (so every permission survives the update) and reopens JARVIS.
It quits this running copy itself, so it runs in its own session and outlives us.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger("jarvis.update")

INSTALLER = ("https://raw.githubusercontent.com/BireshwarChakravarty/JarvisServer/"
             "claude/eager-newton-yifemd/scripts/install-app.sh")


def start_update(home: Path) -> Path:
    logfile = home / "update.log"
    cmd = f'/usr/bin/curl -fsSL "{INSTALLER}" | /bin/bash >> "{logfile}" 2>&1'
    with open(logfile, "a") as fh:
        fh.write("\n── update started ──\n")
    subprocess.Popen(["/bin/bash", "-c", cmd], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log.info("update started; log at %s", logfile)
    return logfile
