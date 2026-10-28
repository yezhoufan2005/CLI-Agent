"""Anthropic Messages API adapter (non-streaming)."""

from __future__ import annotations

from typing import Any

import httpx

from cli_agent.messages import (
    Completion,
    Message,
    StopReason,
    TextBlock,
    ToolUseBlock,
    Usage,
    message_to_dict,
)
from cli_agent.providers.base import (
    RETRYABLE_STATUS,
    ProviderError,
    ToolSpec,
    parse_retry_after,
)

API_VERSION = "2023-06-01"

_STOP_MAP: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://api.anthropic.com",
        api_key: str | None = None,
        auth_token: str | None = None,
        timeout: float = 600.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key and not auth_token:
            raise ProviderError(
                "no Anthropic credentials: set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN"
            )
        self.model = model
        headers = {"anthropic-version": API_VERSION, "content-type": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        if auth_token:
            headers["authorization"] = f"Bearer {auth_token}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=30.0),
            transport=transport,
        )

    def build_request(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> dict[str, Any]:
        wire_messages = [message_to_dict(m) for m in messages]
        # Cache the stable prefix (system + tools) and the conversation up to the
        # latest turn so long sessions don't re-pay for the whole history.
        if wire_messages and wire_messages[-1]["content"]:
            last_block = dict(wire_messages[-1]["content"][-1])
            last_block["cache_control"] = {"type": "ephemeral"}
            wire_messages[-1] = {
                **wire_messages[-1],
                "content": [*wire_messages[-1]["content"][:-1], last_block],
            }
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": wire_messages,
        }
        if system:
            body["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ]
        return body

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> Completion:
        body = self.build_request(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        try:
            resp = self._client.post("/v1/messages", json=body)
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


def _error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error", {})
        if isinstance(err, dict) and err.get("message"):
            return f"{err.get('type', 'error')}: {err['message']}"
    except ValueError:
        pass
    return resp.text[:500]


def parse_response(data: dict[str, Any]) -> Completion:
    if data.get("type") == "error":
        err = data.get("error") or {}
        raise ProviderError(
            f"{err.get('type', 'error')}: {err.get('message', 'unknown')}",
            retryable=err.get("type") in ("overloaded_error", "api_error", "rate_limit_error"),
        )
    blocks: list[TextBlock | ToolUseBlock] = []
    for raw in data.get("content") or []:
        kind = raw.get("type")
        if kind == "text":
            blocks.append(TextBlock(text=raw.get("text", "")))
        elif kind == "tool_use":
            tool_input = raw.get("input")
            blocks.append(
                ToolUseBlock(
                    id=str(raw["id"]),
                    name=str(raw["name"]),
                    input=tool_input if isinstance(tool_input, dict) else {},
                )
            )
        # Other block types (thinking, server tools) are ignored in this version.
    usage_raw = data.get("usage") or {}
    usage = Usage(
        input_tokens=int(usage_raw.get("input_tokens") or 0),
        output_tokens=int(usage_raw.get("output_tokens") or 0),
        cache_read_tokens=int(usage_raw.get("cache_read_input_tokens") or 0),
        cache_write_tokens=int(usage_raw.get("cache_creation_input_tokens") or 0),
    )
    stop = _STOP_MAP.get(str(data.get("stop_reason")), "other")
    if stop == "end_turn" and any(isinstance(b, ToolUseBlock) for b in blocks):
        stop = "tool_use"
    return Completion(
        message=Message(role="assistant", content=list(blocks)),
        stop_reason=stop,
        usage=usage,
        model=str(data.get("model", "")),
    )
