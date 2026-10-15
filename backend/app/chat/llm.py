"""Optional LLM layer (PROJECT_CONTEXT section 11): an OpenAI-compatible chat-completions call.

Works with OpenAI, Groq, Gemini's OpenAI endpoint, etc. through LLM_BASE_URL / LLM_API_KEY / LLM_MODEL.
The LLM only (1) picks one of our tools and its arguments and (2) phrases the answer from the
tool's result. It never sees the database and never writes SQL. Any problem raises LLMError and
the caller uses the keyword fallback instead.
"""
import json
import logging
from typing import Any, Callable

import httpx

from app.chat.tools import TOOL_SCHEMAS, ToolResult
from app.core.config import get_settings

logger = logging.getLogger("app.chat.llm")

TIMEOUT_SECONDS = 20
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
SYSTEM_PROMPT = (
    "You are the assistant of Trade Perfect, a trade settlement risk tool. Answer questions about trades, "
    "counterparties, symbols and rules ONLY by calling one of the tools. Never invent numbers or trade details. "
    "After a tool result, answer in one or two plain sentences using exactly the figures from the result. "
    "If no tool fits, say what you can help with."
)


class LLMError(Exception):
    pass


def enabled() -> bool:
    return bool(get_settings().llm_api_key)


def _complete(messages: list[dict], with_tools: bool) -> dict:
    settings = get_settings()
    payload: dict[str, Any] = {"model": settings.llm_model or DEFAULT_MODEL, "messages": messages, "temperature": 0}
    if with_tools:
        payload["tools"] = [{"type": "function", "function": schema} for schema in TOOL_SCHEMAS]
        payload["tool_choice"] = "auto"
    url = (settings.llm_base_url or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
    try:
        response = httpx.post(url, json=payload, timeout=TIMEOUT_SECONDS,
                              headers={"Authorization": f"Bearer {settings.llm_api_key}"})
        response.raise_for_status()
        return response.json()["choices"][0]["message"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError(f"LLM call failed: {type(exc).__name__}") from exc   # never log the key or the full error body


def ask(question: str, run_tool: Callable[[str, dict], ToolResult]) -> tuple[str, str | None, Any]:
    """(answer, tool used, data). Raises LLMError when the LLM cannot be used for this question."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]
    first = _complete(messages, with_tools=True)
    calls = first.get("tool_calls") or []
    if not calls:
        content = (first.get("content") or "").strip()
        if not content:
            raise LLMError("LLM returned neither a tool call nor an answer")
        return content, None, None
    call = calls[0]
    try:
        name = call["function"]["name"]
        arguments = json.loads(call["function"].get("arguments") or "{}")
        result = run_tool(name, arguments)
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"LLM asked for a tool call we cannot run: {exc}") from exc

    try:  # let the LLM phrase it; if that fails the tool's own sentence is already a good answer
        follow_up = messages + [
            {"role": "assistant", "content": first.get("content"), "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call.get("id", "call_0"),
             "content": json.dumps({"summary": result.answer, "data": result.data}, default=str)[:6000]}]
        phrased = (_complete(follow_up, with_tools=False).get("content") or "").strip()
    except LLMError:
        logger.warning("LLM could not phrase the answer - using the tool's own text")
        phrased = ""
    return phrased or result.answer, name, result.data
