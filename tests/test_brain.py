import asyncio
import json

from fakes import FakeClient, message, text_block, tool_block

from jarvis.core.brain import Brain
from jarvis.core.memory import Memory
from jarvis.tools.context import ToolContext
from jarvis.tools.registry import ToolRegistry


def make_brain(cfg, script, tmp_path):
    reg = ToolRegistry()
    seen = []

    @reg.tool("Get the weather", {"city": {"type": "string"}}, ["city"])
    def get_weather(ctx, city):
        seen.append(city)
        return {"city": city, "temperature": 14}

    mem = Memory(tmp_path / "b.db")
    client = FakeClient(script)
    brain = Brain(cfg, reg, mem, ToolContext(cfg=cfg, memory=mem), client=client)
    return brain, client, seen, mem


def test_streams_sentences_and_runs_tools(cfg, tmp_path):
    script = [
        message([text_block("Checking the weather now. "), tool_block("t1", "get_weather", {"city": "London"})],
                stop_reason="tool_use"),
        message([text_block("It's fourteen degrees in London, sir. Rather mild.")]),
    ]
    brain, client, seen, mem = make_brain(cfg, script, tmp_path)
    spoken = []

    async def on_sentence(s):
        spoken.append(s)

    reply = asyncio.run(brain.respond("What's the weather?", on_sentence=on_sentence))
    assert seen == ["London"]
    assert spoken == ["Checking the weather now.", "It's fourteen degrees in London, sir.", "Rather mild."]
    assert reply.endswith("Rather mild.")

    # request shape: cached frozen system prompt, eager tools, server web search, effort, fallbacks
    req = client.requests[0]
    assert req["model"] == "claude-sonnet-5-5"                # speed: balanced (the default)
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["output_config"] == {"effort": "low"}
    assert req["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in req["betas"]
    names = [t.get("name") for t in req["tools"]]
    assert "get_weather" in names and "web_search" in names
    assert next(t for t in req["tools"] if t["name"] == "get_weather")["eager_input_streaming"] is True
    assert "thinking" not in req  # Opus 5.5: adaptive by default, never 'disabled'

    # second request carries the tool result for t1
    second = client.requests[1]["messages"]
    assert second[-1]["role"] == "user"
    result = second[-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "t1"
    assert json.loads(result["content"])["temperature"] == 14
    # transcript logged to memory
    roles = [t["role"] for t in mem.session_transcript(brain.session.id)]
    assert roles == ["user", "assistant"]


def test_history_is_append_only_across_turns(cfg, tmp_path):
    script = [message([text_block("Hello, sir.")]), message([text_block("Of course.")])]
    brain, client, _, _ = make_brain(cfg, script, tmp_path)
    asyncio.run(brain.respond("hi"))
    asyncio.run(brain.respond("thanks"))
    first, second = client.requests[0], client.requests[1]
    assert first["system"] == second["system"] and first["tools"] == second["tools"]
    # every message sent in request 1 is unchanged at the front of request 2
    assert second["messages"][: len(first["messages"])] == first["messages"]
    # context telemetry rides inside the user turn, never in the system prompt
    texts = [b["text"] for b in second["messages"][-1]["content"]]
    assert texts[0].startswith("[context]") and texts[-1] == "thanks"


def test_session_rolls_over_after_idle(cfg, tmp_path):
    script = [message([text_block("One.")]), message([text_block("Two.")])]
    brain, client, _, mem = make_brain(cfg, script, tmp_path)

    async def run():
        await brain.respond("first")
        first_id = brain.session.id
        brain.session.last_active -= 3600  # an hour of silence
        await brain.respond("second")
        await asyncio.sleep(0.05)  # let the background summary finish
        return first_id

    first_id = asyncio.run(run())
    assert brain.session.id != first_id
    assert len(client.requests[1]["messages"]) == 1  # fresh conversation
    assert mem.recent_summaries()[-1]["summary"] == "summary of the chat"


def test_invalid_tool_input_is_reported_not_run(cfg, tmp_path):
    script = [
        message([tool_block("t9", "get_weather", {"town": "Paris"})], stop_reason="tool_use"),
        message([text_block("Let me try that again.")]),
    ]
    brain, client, seen, _ = make_brain(cfg, script, tmp_path)
    asyncio.run(brain.respond("weather in paris"))
    assert seen == []
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "INVALID_INPUT" in result["content"]


def test_api_failure_is_spoken_and_history_stays_valid(cfg, tmp_path):
    import anthropic
    import httpx2

    class Boom(FakeClient):
        pass

    brain, client, _, _ = make_brain(cfg, [message([text_block("Recovered.")])], tmp_path)
    original = client.beta.messages.stream
    calls = {"n": 0}

    def flaky(**params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
        return original(**params)

    client.beta.messages.stream = flaky
    spoken = []

    async def on_sentence(s):
        spoken.append(s)

    asyncio.run(brain.respond("hello", on_sentence=on_sentence))
    assert spoken and "network" in spoken[0].lower()
    assert brain.session.messages == []  # dangling user turn removed
    asyncio.run(brain.respond("hello again"))
    assert client.requests[-1]["messages"][0]["role"] == "user"
