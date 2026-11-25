"""A real Gemini Live conversation, run by the build when the GEMINI_API_KEY secret is set.

Uses JARVIS's own LiveVoice code (same client, same setup ladder, same tools): connects, asks for a few
words and checks spoken audio comes back, then asks something that needs a tool and checks Gemini calls
it and speaks the result. Exits non-zero, with the reason, if any of that fails.

    GEMINI_API_KEY=… python scripts/live_check.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from types import SimpleNamespace


async def main() -> int:
    from google.genai import types

    from jarvis.config import load_config
    from jarvis.tools.registry import ToolRegistry
    from jarvis.voice import live

    if not os.environ.get(live.KEY_ENV):
        print("no GEMINI_API_KEY: skipped")
        return 0
    cfg = load_config()
    reg = ToolRegistry()

    @reg.tool("The current time in the user's city", {})
    def get_time(ctx):
        return "half past three in the afternoon"

    lv = live.LiveVoice(SimpleNamespace(cfg=cfg, capabilities={}))
    lv._tools = lambda: list(reg.active())

    r = await lv.test()
    print("1. speak:", r)
    if not r.get("ok"):
        return 1

    cm, session, model, level = await lv._open({"time": "unknown: use the get_time tool"})
    called, audio, t0 = [], 0, time.monotonic()
    try:
        await session.send_client_content(turns=types.Content(role="user", parts=[types.Part(
            text="What time is it? Use your tool, then tell me.")]), turn_complete=True)

        async def converse():
            nonlocal audio
            for _ in range(3):                      # the tool call, then the spoken answer
                async for msg in session.receive():
                    if msg.tool_call:
                        for c in msg.tool_call.function_calls:
                            called.append(c.name)
                            await session.send_tool_response(function_responses=[types.FunctionResponse(
                                id=c.id, name=c.name, response={"result": "half past three in the afternoon"})])
                    if msg.data:
                        audio += len(msg.data)
                if audio and called:
                    return
        await asyncio.wait_for(converse(), timeout=45)
    finally:
        await cm.__aexit__(None, None, None)
    print(f"2. tools: model={model} setup={level} called={called} audio={audio} bytes in {time.monotonic() - t0:.1f}s")
    return 0 if ("get_time" in called and audio > 0) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
