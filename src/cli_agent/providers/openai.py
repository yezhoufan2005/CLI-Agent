"""OpenAI-compatible Chat Completions adapter (non-streaming).

Targets the widely implemented ``POST {base_url}/chat/completions`` shape
(OpenAI, OpenRouter, Ollama, vLLM, LiteLLM, ...). Translates the canonical
content-block history to and from role/tool_calls messages.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from cli_agent.messages import (
    Completion,
    Message,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from cli_agent.providers.base import (
    RETRYABLE_STATUS,
    ProviderError,
    ToolSpec,
    parse_retry_after,
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"

_FINISH_MAP: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        timeout: float = 600.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.model = model
        headers = {"content-type": "application/json"}
        # Local servers (Ollama, vLLM) often need no key, so it is optional.
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=30.0),
            transport=transport,
        )

    def build_request(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> dict[str, Any]:
        wire: list[dict[str, Any]] = []
        if system:
            wire.append({"role": "system", "content": system})
        for msg in messages:
            wire.extend(to_wire(msg))
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "messages": wire}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]
        return body

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        on_text: Callable[[str], None] | None = None,
    ) -> Completion:
        # Streaming is not implemented for this provider; the UI prints the
        # full text when the turn completes.
        body = self.build_request(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        try:
            resp = self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException as err:
            raise ProviderError(f"request timed out: {err}", retryable=True) from err
        except httpx.TransportError as err:
            raise ProviderError(f"network error: {err}", retryable=True) from err
        if resp.status_code != 200:
            raise ProviderError(
                f"HTTP {resp.status_code}: {_error_detail(resp)}",
                retryable=resp.status_code in RETRYABLE_STATUS,
                retry_after=parse_retry_after(resp.headers.get("retry-after")),
            )
        try:
            data = resp.json()
        except ValueError as err:
            raise ProviderError("response was not valid JSON", retryable=True) from err
        return parse_response(data)


def to_wire(msg: Message) -> list[dict[str, Any]]:
    """One canonical message becomes one or more Chat Completions messages."""
    if msg.role == "assistant":
        out: dict[str, Any] = {"role": "assistant", "content": msg.text() or None}
        calls = msg.tool_uses()
        if calls:
            out["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.input)},
                }
                for c in calls
            ]
        return [out]
    # Tool results must directly follow the assistant message that requested
    # them, so they come before any user text in the same canonical message.
    wire: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, ToolResultBlock):
            content = f"ERROR: {block.content}" if block.is_error else block.content
            wire.append({"role": "tool", "tool_call_id": block.tool_use_id, "content": content})
    text = "\n\n".join(b.text for b in msg.content if isinstance(b, TextBlock))
    if text:
        wire.append({"role": "user", "content": text})
    return wire


def _error_detail(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error")
        if isinstance(err, dict) and err.get("message"):
            return f"{err.get('type') or err.get('code') or 'error'}: {err['message']}"
        if isinstance(err, str):
            return err
    except (ValueError, AttributeError):
        pass
    return resp.text[:500]


def parse_response(data: dict[str, Any]) -> Completion:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError(f"response has no choices: {json.dumps(data)[:300]}", retryable=True)
    choice = choices[0] or {}
    raw = choice.get("message") or {}
    blocks: list[TextBlock | ToolUseBlock] = []
    content = raw.get("content")
    if isinstance(content, str) and content:
        blocks.append(TextBlock(text=content))
    for i, call in enumerate(raw.get("tool_calls") or []):
        fn = call.get("function") or {}
        args_raw = fn.get("arguments")
        parsed: Any
        if isinstance(args_raw, dict):
            parsed = args_raw  # some servers send an object instead of a JSON string
        else:
            try:
                parsed = json.loads(args_raw) if args_raw and str(args_raw).strip() else {}
            except (json.JSONDecodeError, TypeError):
                parsed = None
        blocks.append(
            ToolUseBlock(
                id=str(call.get("id") or f"call_{i}"),
                name=str(fn.get("name", "")),
                input=parsed if isinstance(parsed, dict) else {},
                complete=isinstance(parsed, dict),
            )
        )
    usage_raw = data.get("usage") or {}
    details = usage_raw.get("prompt_tokens_details") or {}
    usage = Usage(
        input_tokens=int(usage_raw.get("prompt_tokens") or 0),
        output_tokens=int(usage_raw.get("completion_tokens") or 0),
        cache_read_tokens=int(details.get("cached_tokens") or 0),
    )
    stop = _FINISH_MAP.get(str(choice.get("finish_reason")), "other")
    if any(isinstance(b, ToolUseBlock) for b in blocks) and stop in ("end_turn", "other"):
        stop = "tool_use"
    return Completion(
        message=Message(role="assistant", content=list(blocks)),
        stop_reason=stop,
        usage=usage,
        model=str(data.get("model", "")),
    )
