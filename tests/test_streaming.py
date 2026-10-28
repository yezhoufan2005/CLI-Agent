"""Contract tests for the Anthropic streaming (SSE) path.

`fixtures/anthropic_stream_tool_use.sse` is a real stream recorded from the
Messages API (text, a thinking block, ping, and a tool_use with
input_json_delta), with proxy-specific billing fields removed.
"""

import json
from pathlib import Path

import httpx
import pytest

from cli_agent.messages import Message, TextBlock, ToolUseBlock
from cli_agent.providers.anthropic import AnthropicProvider
from cli_agent.providers.base import ProviderError

FIXTURE = Path(__file__).parent / "fixtures" / "anthropic_stream_tool_use.sse"
MSGS = [Message("user", [TextBlock("hi")])]


def sse(*events: dict) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def provider(body: bytes, status: int = 200, seen: dict | None = None) -> AnthropicProvider:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["body"] = json.loads(req.content)
        return httpx.Response(status, content=body, headers={"content-type": "text/event-stream"})

    return AnthropicProvider(
        model="m", base_url="https://api.test", api_key="k", transport=httpx.MockTransport(handler)
    )


def run(p: AnthropicProvider) -> tuple:  # type: ignore[type-arg]
    deltas: list[str] = []
    c = p.complete(system="s", messages=MSGS, tools=[], max_tokens=50, on_text=deltas.append)
    return c, deltas


def test_recorded_stream_with_tool_use() -> None:
    seen: dict = {}
    c, deltas = run(provider(FIXTURE.read_bytes(), seen=seen))
    assert seen["body"]["stream"] is True
    assert c.stop_reason == "tool_use"
    text = c.message.text()
    assert text and "".join(deltas) == text
    calls = c.message.tool_uses()
    assert len(calls) == 1 and calls[0].name == "read_file"
    assert calls[0].input == {"path": "README.md"} and calls[0].complete
    # Thinking blocks are not kept in history.
    assert [type(b) for b in c.message.content] == [TextBlock, ToolUseBlock]
    assert c.usage.output_tokens == 115 and c.usage.input_tokens == 7443


def test_truncated_tool_json_marked_incomplete() -> None:
    body = sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "t1", "name": "write_file", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"path": "a", "content": "abc'},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "max_tokens"},
            "usage": {"output_tokens": 9},
        },
        {"type": "message_stop"},
    )
    c, _ = run(provider(body))
    call = c.message.tool_uses()[0]
    assert not call.complete and call.input == {}
    assert c.stop_reason == "max_tokens"


def test_empty_tool_input_is_empty_dict() -> None:
    body = sse(
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "t1", "name": "list_files", "input": {}},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    )
    c, _ = run(provider(body))
    assert c.message.tool_uses()[0].input == {} and c.message.tool_uses()[0].complete


def test_mid_stream_overloaded_error_is_retryable() -> None:
    body = sse(
        {"type": "message_start", "message": {}},
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    with pytest.raises(ProviderError) as info:
        run(provider(body))
    assert info.value.retryable and "Overloaded" in str(info.value)


def test_stream_cut_before_message_stop_is_retryable() -> None:
    body = sse(
        {"type": "message_start", "message": {}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "par"}},
    )
    with pytest.raises(ProviderError, match="before message_stop") as info:
        run(provider(body))
    assert info.value.retryable


def test_malformed_event_is_retryable() -> None:
    with pytest.raises(ProviderError, match="malformed") as info:
        run(provider(b"event: x\ndata: {not json\n\n"))
    assert info.value.retryable


def test_unknown_events_ignored() -> None:
    body = sse(
        {"type": "message_start", "message": {}},
        {"type": "brand_new_event", "foo": 1},
        {"type": "ping"},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "future_delta", "x": 1}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    )
    c, deltas = run(provider(body))
    assert c.message.text() == "ok" and deltas == ["ok"] and c.stop_reason == "end_turn"


def test_http_error_on_stream_request() -> None:
    body = json.dumps({"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}})
    with pytest.raises(ProviderError) as info:
        run(provider(body.encode(), status=429))
    assert info.value.retryable and "slow" in str(info.value)


def test_non_streaming_still_works() -> None:
    body = json.dumps({"content": [{"type": "text", "text": "hey"}], "stop_reason": "end_turn"})
    c = provider(body.encode()).complete(system="", messages=MSGS, tools=[], max_tokens=5)
    assert c.message.text() == "hey"
