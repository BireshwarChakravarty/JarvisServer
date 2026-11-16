"""The plan brain recovers by itself: a CLI that quits silently is retried in a fresh session, and a
turn that hangs is stopped instead of freezing JARVIS."""
import asyncio
import stat
import sys
import textwrap

import pytest

FAKE = textwrap.dedent('''\
    #!{py}
    import json, os, sys, time
    mode, marker = os.environ["FAKE_MODE"], os.environ["FAKE_MARKER"]
    if mode == "quit-first" and not os.path.exists(marker):
        open(marker, "w").close()
        sys.exit(1)                      # e.g. "No conversation found with session ID"
    for line in sys.stdin:
        if mode == "hang":
            time.sleep(60)
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                          "result": "Back online, sir.", "usage": {{}}}}), flush=True)
''')


def make_brain(cfg, tmp_path, mode):
    from jarvis.core.cli_brains import ClaudeCodeBrain
    from jarvis.core.memory import Memory
    from jarvis.tools.context import ToolContext
    from jarvis.tools.registry import ToolRegistry

    exe = tmp_path / "claude"
    exe.write_text(FAKE.format(py=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    cfg.data["brain"].update(provider="claude-code", claude_path=str(exe), turn_timeout_seconds=2,
                             cli_env={"FAKE_MODE": mode, "FAKE_MARKER": str(tmp_path / "ran")})
    mem = Memory(tmp_path / "m.db")
    return ClaudeCodeBrain(cfg, ToolRegistry(), mem, ToolContext(cfg=cfg, memory=mem))


@pytest.mark.skipif(sys.platform == "win32", reason="posix script")
def test_silent_exit_is_retried_in_a_fresh_session(cfg, tmp_path):
    brain = make_brain(cfg, tmp_path, "quit-first")
    spoken = []

    async def run():
        first = brain.session_id
        r = await brain.respond("Status?", on_sentence=lambda s: spoken.append(s) or asyncio.sleep(0))
        await brain._stop(); await brain.mcp.stop()
        return r, first

    reply, _ = asyncio.run(run())
    assert reply == "Back online, sir." and "stopped unexpectedly" not in " ".join(spoken)


@pytest.mark.skipif(sys.platform == "win32", reason="posix script")
def test_a_hung_turn_is_stopped(cfg, tmp_path):
    brain = make_brain(cfg, tmp_path, "hang")

    async def run():
        t0 = asyncio.get_running_loop().time()
        r = await brain.respond("Status?")
        took = asyncio.get_running_loop().time() - t0
        await brain._stop(); await brain.mcp.stop()
        return r, took

    reply, took = asyncio.run(run())
    assert "taking far too long" in reply and took < 10
