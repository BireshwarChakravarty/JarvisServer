"""How quickly JARVIS answers versus how hard he thinks.

  fast      (default) a quick model with little deliberation: about a second to his first word,
            like talking to a person. Plenty for everyday requests, tools and conversation.
  balanced  a stronger model, still with little deliberation: a beat slower, better at harder asks.
  thorough  your chosen model, thinking more before answering: noticeably slower.

Current Claude models can't switch thinking off entirely; "effort" is how much they deliberate.
"""
from __future__ import annotations

SPEEDS = ("fast", "balanced", "thorough")


def speed(cfg) -> str:
    s = str(cfg.get("brain.speed", "fast") or "fast").lower()
    return s if s in SPEEDS else "fast"


def claude_code(cfg) -> tuple[str | None, str]:
    """(model alias or None for the plan default, effort) for the Claude Code CLI."""
    s = speed(cfg)
    chosen = cfg.get("brain.claude_model") or None
    if s == "fast":
        return "haiku", "low"
    if s == "balanced":
        return chosen or "sonnet", "low"
    return chosen, "medium"


def api(cfg) -> tuple[str, str]:
    """(model id, effort) for the API-key brain."""
    s = speed(cfg)
    if s == "fast":
        return "claude-haiku-5-5", "low"
    if s == "balanced":
        return "claude-sonnet-5-5", "low"
    return str(cfg.get("brain.model", "claude-opus-5-5")), str(cfg.get("brain.effort", "medium") or "medium")


def codex(cfg) -> str:
    """Reasoning effort for the ChatGPT (Codex) CLI."""
    return "medium" if speed(cfg) == "thorough" else "low"
