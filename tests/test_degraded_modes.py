"""JARVIS degrades gracefully: missing pieces give a helpful spoken answer, never a crash or silence."""
import asyncio


def test_plan_brain_without_claude_code_installed_says_how_to_fix_it(cfg, monkeypatch, tmp_path):
    from jarvis.core.orchestrator import Jarvis

    monkeypatch.setenv("PATH", str(tmp_path))                     # no `claude` anywhere
    cfg.data["brain"].update(provider="claude-code", claude_path=str(tmp_path / "nope"))

    async def run():
        app = Jarvis(cfg, headless=True)
        await app.start()
        reply = await app.handle_text("What's on today?", source="text")
        await app.brain.mcp.stop() if getattr(app.brain, "mcp", None) else None
        return reply

    reply = asyncio.run(run())
    assert "Claude Code isn't installed" in reply and "Settings" in reply


def test_hand_control_without_a_camera_explains(cfg):
    from jarvis.core.orchestrator import Jarvis

    app = Jarvis(cfg, headless=True)
    assert "needs the camera" in app.set_hand_control(True)
    assert "already off" in app.set_hand_control(False)


def test_a_crashing_brain_is_spoken_not_silent(cfg):
    from jarvis.core.orchestrator import Jarvis

    async def run():
        app = Jarvis(cfg, headless=True)
        await app.start()

        async def boom(*a, **k):
            raise RuntimeError("unexpected")

        app.brain.respond = boom
        return await app.handle_text("Do something", source="text")

    reply = asyncio.run(run())
    assert reply.startswith("Sorry") and "try that again" in reply
