import asyncio

from jarvis.tools.registry import ImageResult, ToolRegistry, validate


def test_validate_types_and_required():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}, "s": {"type": "string", "enum": ["a", "b"]}},
              "required": ["n"], "additionalProperties": False}
    assert validate(schema, {"n": 3, "s": "a"}) == []
    assert any("required" in e for e in validate(schema, {}))
    assert any("expected integer" in e for e in validate(schema, {"n": "3"}))
    assert any("not one of" in e for e in validate(schema, {"n": 1, "s": "z"}))
    assert any("unexpected" in e for e in validate(schema, {"n": 1, "x": 1}))
    assert any("expected integer" in e for e in validate(schema, {"n": True}))


def test_schema_and_call_with_confirmation():
    reg = ToolRegistry()
    calls = []

    @reg.tool("Do a thing", {"x": {"type": "integer"}}, ["x"], confirm=True, confirm_message="do {x} things")
    def do_thing(ctx, x):
        calls.append(x)
        return {"ok": x}

    schema = reg.schemas()[0]
    assert schema["name"] == "do_thing" and schema["eager_input_streaming"] is True
    assert schema["input_schema"]["additionalProperties"] is False

    asked = []

    class Ctx:
        require_confirmation = True

        async def confirm(self, action):
            asked.append(action)
            return len(asked) > 1  # decline first, approve second

    ctx = Ctx()
    r1 = asyncio.run(reg.call("do_thing", {"x": 2}, ctx))
    assert r1.is_error and "declined" in r1.content and calls == []
    r2 = asyncio.run(reg.call("do_thing", {"x": 2}, ctx))
    assert not r2.is_error and calls == [2] and '"ok": 2' in r2.content
    assert asked == ["do 2 things", "do 2 things"]


def test_invalid_input_and_exceptions_become_errors():
    reg = ToolRegistry()

    @reg.tool("Boom", {"x": {"type": "string"}}, ["x"])
    async def boom(ctx, x):
        raise RuntimeError("kaboom")

    bad = asyncio.run(reg.call("boom", {"y": 1}, object()))
    assert bad.is_error and "INVALID_INPUT" in bad.content
    err = asyncio.run(reg.call("boom", {"x": "1"}, object()))
    assert err.is_error and "kaboom" in err.content
    missing = asyncio.run(reg.call("nope", {}, object()))
    assert missing.is_error


def test_image_results_become_image_blocks():
    reg = ToolRegistry()

    @reg.tool("Pic")
    def pic(ctx):
        return ImageResult(b"\xff\xd8jpeg", "image/jpeg", "a frame")

    r = asyncio.run(reg.call("pic", {}, object()))
    assert r.content[0]["type"] == "image" and r.content[0]["source"]["media_type"] == "image/jpeg"
    assert r.content[1] == {"type": "text", "text": "a frame"}
    assert "[+1 image]" in r.summary


def test_builtin_tools_have_valid_schemas():
    import jarvis.tools.builtin  # noqa: F401
    from jarvis.tools.registry import registry

    names = registry.names()
    assert len(names) >= 40
    assert names == sorted(names)  # deterministic order keeps the prompt cache warm
    for s in registry.schemas():
        assert len(s["name"]) <= 64 and s["description"]
        assert s["input_schema"]["type"] == "object"
        for req in s["input_schema"]["required"]:
            assert req in s["input_schema"]["properties"]
    risky = {t.name for t in registry.active() if t.confirm}
    assert {"send_message", "run_shell", "run_applescript"} <= risky
