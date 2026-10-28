"""OpenAI-compatible Chat Completions adapter (streaming via SSE, or a single JSON response).

Targets the widely implemented ``POST {base_url}/chat/completions`` shape
(OpenAI, OpenRouter, Ollama, vLLM, LiteLLM, ...). Translates the canonical
content-block history to and from role/tool_calls messages.
"""

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
        body = self.build_request(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        if on_text is not None:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        try:
            with self._client.stream("POST", "/chat/completions", json=body) as resp:
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
                    return _parse_safely(lambda: parse_response(data))
                lines = resp.iter_lines()
                return _parse_safely(lambda: parse_response(accumulate_stream(lines, on_text)))
        except httpx.TimeoutException as err:
            raise ProviderError(f"request timed out: {err}", retryable=True) from err
        except httpx.TransportError as err:
            raise ProviderError(f"network error: {err}", retryable=True) from err


def _parse_safely(parse: Callable[[], Completion]) -> Completion:
    try:
        return parse()
    except ProviderError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, IndexError) as err:
        raise ProviderError(
            f"malformed response from server ({type(err).__name__})", retryable=True
        ) from err


def accumulate_stream(lines: Iterator[str], on_text: Callable[[str], None]) -> dict[str, Any]:
    """Rebuild a non-streaming response body from Chat Completions SSE chunks.

    Tool-call fragments are joined by their `index`; the id and name arrive in
    the first fragment and the JSON arguments are split across the rest.
    """
    text: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    finish: str | None = None
    usage: dict[str, Any] = {}
    model = ""
    done = False
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            done = True
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError as err:
            raise ProviderError(
                f"malformed stream chunk ({len(payload)} bytes)", retryable=True
            ) from err
        if not isinstance(chunk, dict):
            raise ProviderError("malformed stream chunk", retryable=True)
        if isinstance(chunk.get("error"), dict):
            err_text = str(chunk["error"].get("message", "unknown"))
            # A context-length error will not go away on retry; let the agent compact.
            overflow = "context" in err_text.lower() and "length" in err_text.lower()
            raise ProviderError(f"stream error: {err_text}", retryable=not overflow)
        model = str(chunk.get("model") or model)
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if isinstance(piece, str) and piece:
                text.append(piece)
                on_text(piece)
            for frag in delta.get("tool_calls") or []:
                if "index" in frag:
                    index = int(frag["index"])
                elif frag.get("id") or not calls:
                    index = len(calls)  # no index: a new id starts a new call
                else:
                    index = max(calls)  # no index, no id: continues the last call
                slot = calls.setdefault(index, {"id": "", "name": "", "args": []})
                if frag.get("id"):
                    slot["id"] = frag["id"]
                fn = frag.get("function") or {}
                if fn.get("name"):
                    # Some servers repeat the full name in every fragment.
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"].append(str(fn["arguments"]))
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    if not done and finish is None:
        raise ProviderError("stream ended before completion", retryable=True)
    message: dict[str, Any] = {"content": "".join(text) or None}
    if calls:
        message["tool_calls"] = [
            {"id": c["id"], "function": {"name": c["name"], "arguments": "".join(c["args"])}}
            for _, c in sorted(calls.items())
        ]
    return {
        "model": model,
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": usage,
    }


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
    if not isinstance(data, dict):
        raise ProviderError("response is not a JSON object", retryable=True)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("response has no choices", retryable=True)
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
