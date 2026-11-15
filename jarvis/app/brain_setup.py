"""Install / sign in / verify the official Claude Code and Codex CLIs from the GUI, so JARVIS
can run on your Claude Pro/Max or ChatGPT plan with no Terminal steps."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess

from jarvis.aio import spawn
from jarvis.core.cli_brains import child_env, find_binary
from jarvis.events import bus

log = logging.getLogger("jarvis.brain_setup")

INSTALLERS = {
    "claude": "curl -fsSL https://claude.ai/install.sh | bash",
    "codex": "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
}
LOGIN = {
    "claude": ["auth", "login", "--claudeai"],
    "codex": ["login"],
}
URL_RE = re.compile(r"https://[^\s\"'<>]+")


def _run(args: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=child_env())
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def status(which: str, configured_path: str | None = None) -> dict:
    binary = find_binary(which, configured_path)
    out: dict = {"installed": bool(binary), "path": binary, "logged_in": False, "plan": False, "detail": ""}
    if not binary:
        return out
    code, ver = _run([binary, "--version"], 15)
    out["version"] = ver.strip().splitlines()[0] if ver.strip() else ""
    if which == "claude":
        code, txt = _run([binary, "auth", "status", "--json"], 20)
        try:
            data = json.loads(txt[txt.index("{"):])
            out["logged_in"] = bool(data.get("loggedIn"))
            out["auth_method"] = data.get("authMethod")
            # claude.ai / oauth_token = your subscription; api_key/console = pay-per-use billing
            out["plan"] = out["logged_in"] and data.get("authMethod") in ("claude.ai", "oauth_token")
            out["detail"] = data.get("email") or data.get("subscriptionType") or ""
            out["subscription"] = str(data.get("subscriptionType") or "").lower()   # e.g. "pro", "max"
        except Exception:
            out["detail"] = txt.strip()[:200]
    else:
        code, txt = _run([binary, "login", "status"], 20)
        out["logged_in"] = code == 0
        out["plan"] = code == 0 and "chatgpt" in txt.lower()
        out["detail"] = txt.strip()[:200]
    return out


class CLISession:
    """A running install or login process whose output streams to the setup screen."""

    def __init__(self, which: str, kind: str) -> None:
        self.which, self.kind = which, kind
        self.proc: asyncio.subprocess.Process | None = None
        self.lines: list[str] = []
        self.opened_url = False
        self.done = asyncio.Event()
        self.code: int | None = None

    async def start(self) -> None:
        if self.kind == "install":
            cmd = ["/bin/bash", "-lc", INSTALLERS[self.which]]
        else:
            binary = find_binary(self.which)
            if not binary:
                raise FileNotFoundError(f"{self.which} isn't installed yet")
            cmd = [binary, *LOGIN[self.which]]
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=child_env({"BROWSER": "open"}))
        spawn(self._pump())

    async def _pump(self) -> None:
        assert self.proc is not None
        while True:
            raw = await self.proc.stdout.readline()
            if not raw:
                break
            line = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw.decode(errors="replace")).rstrip()
            if not line:
                continue
            self.lines.append(line)
            bus.emit("cli_log", which=self.which, kind=self.kind, line=line[:400])
            if self.kind == "login" and not self.opened_url:
                m = URL_RE.search(line)
                if m:  # make sure the browser opens even if the CLI couldn't
                    self.opened_url = True
                    subprocess.Popen(["/usr/bin/open", m.group(0)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.code = await self.proc.wait()
        bus.emit("cli_log", which=self.which, kind=self.kind, done=True, code=self.code)
        self.done.set()

    async def send(self, text: str) -> None:
        if self.proc and self.proc.stdin and self.proc.returncode is None:
            self.proc.stdin.write((text.strip() + "\n").encode())
            await self.proc.stdin.drain()

    def cancel(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
