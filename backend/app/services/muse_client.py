"""
muse_client.py

One chat completion with tools against the Meta Model API (OpenAI-compatible
format, confirmed by gate G3). Raises AgentError instead of returning a guess.
"""

from __future__ import annotations

import httpx

from app.config import AGENT_MAX_TOKENS, AGENT_MODEL, MODEL_API_BASE, MODEL_API_KEY


class AgentError(Exception):
    def __init__(self, status: str, reason: str):
        super().__init__(f"{status}: {reason}")
        self.status = status  # Failed | TimedOut
        self.reason = reason


def chat(messages: list[dict], tools: list[dict], tool_choice: str, timeout: float) -> dict:
    """Returns {"message", "finish_reason", "usage"} for the first choice."""
    if not MODEL_API_KEY:
        raise AgentError("Failed", "MODEL_API_KEY is not set")
    body = {"model": AGENT_MODEL, "messages": messages, "tools": tools, "tool_choice": tool_choice, "max_tokens": AGENT_MAX_TOKENS}
    try:
        resp = httpx.post(
            f"{MODEL_API_BASE}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {MODEL_API_KEY}"},
            timeout=timeout,
        )
    except httpx.TimeoutException:
        raise AgentError("TimedOut", f"no response within {timeout:.1f} s")
    except httpx.HTTPError as exc:
        raise AgentError("Failed", f"request error: {type(exc).__name__}")
    if resp.status_code != 200:
        raise AgentError("Failed", f"HTTP {resp.status_code}: {resp.text[:200]}")
    payload = resp.json()
    choice = payload["choices"][0]
    return {"message": choice["message"], "finish_reason": choice.get("finish_reason"), "usage": payload.get("usage", {})}
