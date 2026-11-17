"""Response speed: fast by default (quick model, little deliberation), with smarter options."""
from types import SimpleNamespace

from jarvis.core import speed


def cfg(**brain):
    data = {"brain." + k: v for k, v in brain.items()}
    return SimpleNamespace(get=lambda k, d=None: data.get(k, d))


def test_fast_is_the_default_everywhere():
    c = cfg()
    assert speed.speed(c) == "fast"
    assert speed.claude_code(c) == ("haiku", "low")
    assert speed.api(c) == ("claude-haiku-5-5", "low")
    assert speed.codex(c) == "low"
    assert speed.speed(cfg(speed="warp")) == "fast"              # unknown values fall back safely


def test_balanced_and_thorough_respect_the_chosen_model():
    assert speed.claude_code(cfg(speed="balanced")) == ("sonnet", "low")
    assert speed.claude_code(cfg(speed="balanced", claude_model="opus")) == ("opus", "low")
    assert speed.claude_code(cfg(speed="thorough")) == (None, "medium")          # the plan's default model
    assert speed.claude_code(cfg(speed="Thorough", claude_model="opus")) == ("opus", "medium")
    assert speed.api(cfg(speed="balanced")) == ("claude-sonnet-5-5", "low")
    assert speed.api(cfg(speed="thorough", model="claude-opus-5-5")) == ("claude-opus-5-5", "medium")
    assert speed.codex(cfg(speed="thorough")) == "medium"


def test_claude_cli_gets_the_model_flag_and_low_effort_env(tmp_path):
    from jarvis.core.cli_brains import ClaudeCodeBrain

    b = ClaudeCodeBrain.__new__(ClaudeCodeBrain)
    b.cfg = cfg(speed="fast", cli_env={"EXTRA": "1"})
    b.session_id = "s"
    b.system_prompt = lambda: "sys"
    args = b._args("http://127.0.0.1:1/mcp", resume=False)
    assert args[args.index("--model") + 1] == "haiku"
    env = b._env()
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "low" and env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1" and env["EXTRA"] == "1"
    b.cfg = cfg(speed="thorough")
    assert "--model" not in b._args("u", resume=True) and b._env()["CLAUDE_CODE_EFFORT_LEVEL"] == "medium"


def test_codex_gets_low_reasoning_effort():
    from jarvis.core.cli_brains import CodexBrain

    b = CodexBrain.__new__(CodexBrain)
    b.cfg = cfg()
    b.thread_id = None
    args = b._args("http://127.0.0.1:1/mcp", "hi")
    assert 'model_reasoning_effort="low"' in args
