"""A scripted stand-in for anthropic.AsyncAnthropic so the brain can be tested offline."""
from __future__ import annotations

import copy
from types import SimpleNamespace as NS


def text_block(text):
    return NS(type="text", text=text)


def tool_block(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


def message(content, stop_reason="end_turn"):
    return NS(content=content, stop_reason=stop_reason, model="claude-opus-5-5",
              usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakeStream:
    def __init__(self, msg, chunk=7):
        self.msg = msg
        self.chunk = chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for block in self.msg.content:
                if block.type == "tool_use":
                    yield NS(type="content_block_start", content_block=block)
                elif block.type == "text":
                    for i in range(0, len(block.text), self.chunk):
                        yield NS(type="text", text=block.text[i:i + self.chunk])
        return gen()

    async def get_final_message(self):
        return self.msg


class FakeClient:
    """Each call to .beta.messages.stream pops the next scripted message."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        outer = self

        class _Messages:
            def stream(self_inner, **params):
                outer.requests.append(copy.deepcopy({k: v for k, v in params.items() if k != "messages"}) |
                                      {"messages": list(params["messages"])})
                return FakeStream(outer.script.pop(0))

            async def create(self_inner, **params):
                return message([text_block("summary of the chat")])

        self.beta = NS(messages=_Messages())
        self.messages = _Messages()
