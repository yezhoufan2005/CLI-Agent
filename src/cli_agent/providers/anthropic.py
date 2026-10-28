"""Anthropic Messages API adapter (streaming via SSE, or a single JSON response)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
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
    "model_context_window_exceeded": "max_tokens",
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
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        on_text: Callable[[str], None] | None = None,
    ) -> Completion:
        """Call the model. With `on_text`, stream and report text as it arrives."""
        body = self.build_request(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        if on_text is not None:
            body["stream"] = True
        try:
            with self._client.stream("POST", "/v1/messages", json=body) as resp:
                if resp.status_code != 200:
                    resp.read()
                    raise ProviderError(
                        f"HTTP {resp.status_code}: {_error_detail(resp)}",
                        retryable=resp.status_code in RETRYABLE_STATUS,
                        retry_after=parse_retry_after(resp.headers.get("retry-after")),
                    )
                if on_text is None:
                    resp.read()
                    try:
                        data = resp.json()
                    except ValueError as err:
                        raise ProviderError("response was not valid JSON", retryable=True) from err
                    return parse_response(data)
                return parse_response(accumulate_stream(_sse_events(resp.iter_lines()), on_text))
        except httpx.TimeoutException as err:
            raise ProviderError(f"request timed out: {err}", retryable=True) from err
        except httpx.TransportError as err:
            raise ProviderError(f"network error: {err}", retryable=True) from err


def _sse_events(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Yield the JSON payload of each server-sent event."""
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                yield _decode_event("\n".join(data))
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        # "event:", "id:", "retry:" and ":" comment lines carry nothing we need;
        # every data payload repeats its type.
    if data:
        yield _decode_event("\n".join(data))


def _decode_event(raw: str) -> dict[str, Any]:
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as err:
        raise ProviderError(f"malformed stream event: {raw[:200]!r}", retryable=True) from err
    if not isinstance(event, dict):
        raise ProviderError(f"malformed stream event: {raw[:200]!r}", retryable=True)
    return event


def accumulate_stream(
    events: Iterator[dict[str, Any]], on_text: Callable[[str], None]
) -> dict[str, Any]:
    """Rebuild a non-streaming response body from Messages API stream events."""
    message: dict[str, Any] = {}
    blocks: dict[int, dict[str, Any]] = {}
    partial_json: dict[int, list[str]] = {}
    done = False
    for event in events:
        kind = event.get("type")
        if kind == "message_start":
            message = dict(event.get("message") or {})
        elif kind == "content_block_start":
            index = int(event.get("index", len(blocks)))
            blocks[index] = dict(event.get("content_block") or {})
            if blocks[index].get("type") == "tool_use":
                partial_json[index] = []
        elif kind == "content_block_delta":
            index = int(event.get("index", 0))
            delta = event.get("delta") or {}
            block = blocks.setdefault(index, {"type": "text", "text": ""})
            if delta.get("type") == "text_delta":
                text = str(delta.get("text", ""))
                block["text"] = block.get("text", "") + text
                if text:
                    on_text(text)
            elif delta.get("type") == "input_json_delta":
                partial_json.setdefault(index, []).append(str(delta.get("partial_json", "")))
        elif kind == "message_delta":
            delta = event.get("delta") or {}
            if "stop_reason" in delta:
                message["stop_reason"] = delta["stop_reason"]
            usage = dict(message.get("usage") or {})
            usage.update(event.get("usage") or {})
            message["usage"] = usage
        elif kind == "message_stop":
            done = True
            break
        elif kind == "error":
            err = event.get("error") or {}
            raise ProviderError(
                f"stream error: {err.get('type', 'error')}: {err.get('message', 'unknown')}",
                retryable=err.get("type") in ("overloaded_error", "api_error", "rate_limit_error"),
            )
        # "ping" and unknown event types are ignored, as the API docs require.
    if not done:
        raise ProviderError("stream ended before message_stop", retryable=True)
    for index, parts in partial_json.items():
        raw = "".join(parts)
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = None  # typically cut off by max_tokens; the agent reports it
        blocks[index]["input"] = parsed
    message["content"] = [blocks[i] for i in sorted(blocks)]
    return message


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
                    complete=isinstance(tool_input, dict),
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
