"""Contract tests for the OpenAI-compatible Chat Completions adapter."""

import json
from pathlib import Path

import httpx
import pytest

from cli_agent.agent import Agent
from cli_agent.config import load_config
from cli_agent.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock
from cli_agent.permissions import PermissionPolicy
from cli_agent.providers.base import ProviderError, ToolSpec
from cli_agent.providers.openai import OpenAIProvider, parse_response, to_wire
from cli_agent.session import Session
from cli_agent.tools import ToolContext, default_tools


def provider(handler) -> OpenAIProvider:  # type: ignore[no-untyped-def]
    return OpenAIProvider(
        model="gpt-x",
        base_url="https://oai.test/v1",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )


def reply(message: dict, finish: str = "stop", **extra) -> dict:  # type: ignore[no-untyped-def]
    return {
        "model": "gpt-x",
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "prompt_tokens_details": {"cached_tokens": 3},
        },
        **extra,
    }


def test_request_translation() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=reply({"role": "assistant", "content": "hi"}))

    history = [
        Message("user", [TextBlock("q")]),
        Message(
            "assistant", [TextBlock("let me look"), ToolUseBlock("c1", "read_file", {"path": "a"})]
        ),
        Message("user", [ToolResultBlock("c1", "boom", is_error=True), TextBlock("also this")]),
    ]
    c = provider(handler).complete(
        system="S",
        messages=history,
        tools=[ToolSpec("read_file", "d", {"type": "object"})],
        max_tokens=9,
    )
    assert seen["url"] == "https://oai.test/v1/chat/completions" and seen["auth"] == "Bearer k"
    msgs = seen["body"]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "user"]
    call = msgs[2]["tool_calls"][0]
    assert call["id"] == "c1" and json.loads(call["function"]["arguments"]) == {"path": "a"}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "ERROR: boom"}
    assert seen["body"]["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert c.message.text() == "hi" and c.stop_reason == "end_turn"
    assert c.usage.input_tokens == 11 and c.usage.cache_read_tokens == 3


def test_tool_calls_parsed_defensively() -> None:
    c = parse_response(
        reply(
            {
                "content": None,
                "tool_calls": [
                    {"id": "a", "function": {"name": "list_files", "arguments": ""}},
                    {"id": "b", "function": {"name": "read_file", "arguments": '{"path": "x"}'}},
                    {
                        "id": "c",
                        "function": {"name": "write_file", "arguments": '{"path": "x", "con'},
                    },
                    {"id": "d", "function": {"name": "read_file", "arguments": {"path": "y"}}},
                    {"id": "e", "function": {"name": "read_file", "arguments": "[1, 2]"}},
                ],
            },
            finish="tool_calls",
        )
    )
    calls = c.message.tool_uses()
    assert [x.input for x in calls] == [{}, {"path": "x"}, {}, {"path": "y"}, {}]
    assert [x.complete for x in calls] == [True, True, False, True, False]
    assert c.stop_reason == "tool_use"


def test_finish_reasons() -> None:
    assert parse_response(reply({"content": "x"}, "length")).stop_reason == "max_tokens"
    assert parse_response(reply({"content": ""}, "content_filter")).stop_reason == "refusal"
    with pytest.raises(ProviderError, match="no choices"):
        parse_response({"choices": []})


@pytest.mark.parametrize(("status", "retryable"), [(429, True), (503, True), (401, False)])
def test_http_errors(status: int, retryable: bool) -> None:
    p = provider(lambda r: httpx.Response(status, json={"error": {"message": "nope", "type": "x"}}))
    with pytest.raises(ProviderError) as info:
        p.complete(system="", messages=[], tools=[], max_tokens=1)
    assert info.value.retryable is retryable and "nope" in str(info.value)


def test_to_wire_plain_user_text() -> None:
    assert to_wire(Message("user", [TextBlock("a"), TextBlock("b")])) == [
        {"role": "user", "content": "a\n\nb"}
    ]


def test_agent_loop_over_openai_adapter(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("data\n")
    replies = iter(
        [
            reply(
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "f.txt"}'},
                        }
                    ],
                },
                finish="tool_calls",
            ),
            reply({"content": "it says data"}),
        ]
    )
    bodies: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=next(replies))

    class QuietUI:
        streams = False

        def on_text_delta(self, t: str) -> None: ...
        def on_text(self, t: str) -> None: ...
        def on_tool_start(self, c, s) -> None: ...  # type: ignore[no-untyped-def]
        def on_tool_end(self, c, r) -> None: ...  # type: ignore[no-untyped-def]
        def on_notice(self, m: str) -> None: ...
        def approve(self, c, s):  # type: ignore[no-untyped-def]
            return "no"

    agent = Agent(
        provider=provider(handler),
        tools=default_tools(),
        policy=PermissionPolicy(mode="auto"),
        ctx=ToolContext(root=tmp_path),
        ui=QuietUI(),
        system="sys",
    )
    result = agent.run(Session(id="s", cwd=str(tmp_path), model="gpt-x"), "read f.txt")
    assert result.status == "done" and result.final_text == "it says data"
    tool_msg = bodies[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "data" in tool_msg["content"]


def test_config_reads_openai_credentials(tmp_path: Path) -> None:
    env = {
        "OPENAI_API_KEY": "ok",
        "OPENAI_BASE_URL": "http://localhost:11434/v1",
        "ANTHROPIC_API_KEY": "ak",
    }
    cfg = load_config(
        None, environ={**env, "CLI_AGENT_CONFIG": str(tmp_path / "none.toml")}, provider="openai"
    )
    assert cfg.provider == "openai" and cfg.api_key == "ok"
    assert cfg.base_url == "http://localhost:11434/v1"
    cfg = load_config(None, environ={**env, "CLI_AGENT_CONFIG": str(tmp_path / "none.toml")})
    assert cfg.provider == "anthropic" and cfg.api_key == "ak" and cfg.base_url == ""


def sse_chunks(*chunks: dict, done: bool = True) -> bytes:  # type: ignore[type-arg]
    body = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks)
    return body + (b"data: [DONE]\n\n" if done else b"")


def stream_provider(body: bytes, seen: dict | None = None) -> OpenAIProvider:  # type: ignore[type-arg]
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return provider(handler)


def test_streaming_text_and_split_tool_calls() -> None:
    body = sse_chunks(
        {"model": "gpt-x", "choices": [{"delta": {"role": "assistant", "content": "Look"}}]},
        {"choices": [{"delta": {"content": "ing"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": ""},
                            }
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"pa'}}]}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 1,
                                "id": "c2",
                                "function": {"name": "list_files", "arguments": "{}"},
                            },
                            {"index": 0, "function": {"arguments": 'th": "a"}'}},
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    )
    seen: dict = {}
    deltas: list[str] = []
    c = stream_provider(body, seen).complete(
        system="", messages=[], tools=[], max_tokens=5, on_text=deltas.append
    )
    assert seen["body"]["stream"] is True and seen["body"]["stream_options"]["include_usage"]
    assert deltas == ["Look", "ing"] and c.message.text() == "Looking"
    calls = c.message.tool_uses()
    assert [(x.id, x.name, x.input) for x in calls] == [
        ("c1", "read_file", {"path": "a"}),
        ("c2", "list_files", {}),
    ]
    assert c.stop_reason == "tool_use" and c.usage.input_tokens == 5 and c.model == "gpt-x"


def test_streaming_truncated_is_retryable() -> None:
    body = sse_chunks({"choices": [{"delta": {"content": "par"}}]}, done=False)
    with pytest.raises(ProviderError) as info:
        stream_provider(body).complete(
            system="", messages=[], tools=[], max_tokens=5, on_text=lambda t: None
        )
    assert info.value.retryable


def test_streaming_without_done_marker_but_finished_is_ok() -> None:
    body = sse_chunks(
        {"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}, done=False
    )
    c = stream_provider(body).complete(
        system="", messages=[], tools=[], max_tokens=5, on_text=lambda t: None
    )
    assert c.message.text() == "x" and c.stop_reason == "end_turn"


def test_streaming_odd_fragment_shapes() -> None:
    body = sse_chunks(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "read_file", "arguments": '{"pa'}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"function": {"name": "read_file", "arguments": 'th": "a"}'}}
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    )
    c = stream_provider(body).complete(
        system="", messages=[], tools=[], max_tokens=5, on_text=lambda t: None
    )
    calls = c.message.tool_uses()
    assert [(x.name, x.input) for x in calls] == [("read_file", {"path": "a"})]


def test_streaming_context_error_not_retryable() -> None:
    body = sse_chunks({"error": {"message": "This model's maximum context length is 8192"}})
    with pytest.raises(ProviderError) as info:
        stream_provider(body).complete(
            system="", messages=[], tools=[], max_tokens=5, on_text=lambda t: None
        )
    assert not info.value.retryable
