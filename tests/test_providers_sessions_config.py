import json
from pathlib import Path

import httpx
import pytest

from cli_agent.config import ConfigError, load_config
from cli_agent.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock
from cli_agent.providers.anthropic import AnthropicProvider, parse_response
from cli_agent.providers.base import ProviderError, RetryPolicy, ToolSpec, with_retries
from cli_agent.session import Session, SessionStore

# --- Anthropic adapter contract tests (wire format per the Messages API) ---


def provider_with(handler) -> AnthropicProvider:  # type: ignore[no-untyped-def]
    return AnthropicProvider(
        model="m",
        base_url="https://api.test",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )


def test_request_shape_and_headers() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "type": "message",
                "model": "m",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {"path": "a"}},
                ],
                "usage": {"input_tokens": 3, "output_tokens": 4, "cache_read_input_tokens": 2},
            },
        )

    p = provider_with(handler)
    msgs = [
        Message("user", [TextBlock("q")]),
        Message("assistant", [ToolUseBlock("tu0", "list_files", {})]),
        Message("user", [ToolResultBlock("tu0", "out", is_error=True)]),
    ]
    c = p.complete(
        system="S",
        messages=msgs,
        tools=[ToolSpec("read_file", "d", {"type": "object"})],
        max_tokens=10,
    )
    assert seen["url"] == "https://api.test/v1/messages"
    assert seen["headers"]["x-api-key"] == "k"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    body = seen["body"]
    assert body["model"] == "m" and body["max_tokens"] == 10
    assert body["system"][0]["text"] == "S"
    assert body["tools"] == [
        {"name": "read_file", "description": "d", "input_schema": {"type": "object"}}
    ]
    last = body["messages"][-1]["content"][-1]
    assert last["type"] == "tool_result" and last["is_error"] is True
    assert last["cache_control"] == {"type": "ephemeral"}
    # The caller's history must not be mutated by cache annotations.
    assert not hasattr(msgs[-1].content[-1], "cache_control")
    assert c.stop_reason == "tool_use" and c.message.tool_uses()[0].input == {"path": "a"}
    assert c.usage.input_tokens == 3 and c.usage.cache_read_tokens == 2


@pytest.mark.parametrize(
    ("status", "retryable"), [(429, True), (529, True), (500, True), (400, False), (401, False)]
)
def test_http_errors_classified(status: int, retryable: bool) -> None:
    p = provider_with(
        lambda req: httpx.Response(
            status,
            headers={"retry-after": "7"},
            json={"type": "error", "error": {"type": "x", "message": "nope"}},
        )
    )
    with pytest.raises(ProviderError) as info:
        p.complete(system="", messages=[], tools=[], max_tokens=1)
    assert info.value.retryable is retryable
    assert info.value.retry_after == 7.0
    assert "nope" in str(info.value)


def test_network_error_retryable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError) as info:
        provider_with(handler).complete(system="", messages=[], tools=[], max_tokens=1)
    assert info.value.retryable


def test_parse_response_edge_cases() -> None:
    c = parse_response(
        {"content": [{"type": "thinking", "thinking": "..."}], "stop_reason": "weird"}
    )
    assert c.message.content == [] and c.stop_reason == "other"
    c = parse_response(
        {
            "content": [{"type": "tool_use", "id": "a", "name": "n", "input": None}],
            "stop_reason": "end_turn",
        }
    )
    assert c.stop_reason == "tool_use" and c.message.tool_uses()[0].input == {}


def test_missing_credentials() -> None:
    with pytest.raises(ProviderError, match="credentials"):
        AnthropicProvider(model="m")


def test_with_retries_honours_retry_after() -> None:
    waits: list[float] = []
    calls = iter([ProviderError("a", retryable=True, retry_after=2.5), "ok"])

    def call():  # type: ignore[no-untyped-def]
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    assert with_retries(call, RetryPolicy(), sleep=waits.append) == "ok"  # type: ignore[arg-type,comparison-overlap]
    assert waits == [2.5]


# --- Sessions ---


def test_session_round_trip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    s = Session(id="abc", cwd="/w", model="m")
    s.add_user_text("hello   world")
    s.messages.append(Message("assistant", [ToolUseBlock("t", "n", {"k": [1]})]))
    s.messages.append(Message("user", [ToolResultBlock("t", "r", True)]))
    store.save(s)
    loaded = store.load("abc")
    assert loaded.messages == s.messages and loaded.title == "hello world"
    assert (store.dir / "abc.json").stat().st_mode & 0o777 == 0o600
    assert [x.id for x in store.list()] == ["abc"]
    assert store.latest_for("/w").id == "abc"  # type: ignore[union-attr]
    assert store.latest_for("/other") is None


def test_session_store_rejects_bad_ids_and_corruption(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    for bad in ("../x", "", ".hidden"):
        with pytest.raises(ValueError):
            store.path(bad)
    store.dir.mkdir(parents=True)
    (store.dir / "bad.json").write_text("{not json")
    with pytest.raises(ValueError, match="corrupt"):
        store.load("bad")
    assert store.list() == []  # corrupt files are skipped, not fatal
    with pytest.raises(FileNotFoundError):
        store.load("missing")


# --- Config ---


def test_config_layers(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text(
        'model = "file-model"\nmode = "auto-edit"\nallow = ["run_shell:pytest*"]\n'
        "shell_timeout = 30\n"
    )
    cfg = load_config(f, environ={"CLI_AGENT_MODEL": "env-model", "ANTHROPIC_API_KEY": "k"})
    assert cfg.model == "env-model" and cfg.mode == "auto-edit"
    assert cfg.allow == ["run_shell:pytest*"] and cfg.shell_timeout == 30.0
    assert cfg.redacted()["api_key"] == "set" and "k" not in json.dumps(cfg.redacted()["allow"])


@pytest.mark.parametrize(
    "content",
    [
        'api_key = "sk"',
        "bogus = 1",
        'mode = "yolo"',
        "max_turns = 0",
        "allow = [1]",
        "max_turns = true",
        "not toml [",
    ],
)
def test_config_rejects_bad_files(tmp_path: Path, content: str) -> None:
    f = tmp_path / "c.toml"
    f.write_text(content)
    with pytest.raises(ConfigError):
        load_config(f, environ={})


def test_config_missing_explicit_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml", environ={})


def test_session_save_fixes_loose_temp_permissions(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.dir.mkdir(parents=True)
    leftover = store.dir / "abc.json.tmp"
    leftover.write_text("{}")
    leftover.chmod(0o644)
    store.save(Session(id="abc", cwd="/w", model="m"))
    assert (store.dir / "abc.json").stat().st_mode & 0o777 == 0o600


def test_project_instructions_skip_symlinks(tmp_path: Path) -> None:
    from cli_agent.prompt import build_system_prompt

    outside = tmp_path / "outside.md"
    outside.write_text("SECRET-CONTENT")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").symlink_to(outside)
    (ws / "CLAUDE.md").write_text("use tabs")
    prompt = build_system_prompt(ws)
    assert "SECRET-CONTENT" not in prompt and "use tabs" in prompt


def test_context_chars_config(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text("context_chars = 50000\n")
    assert load_config(f, environ={}).context_chars == 50000
    f.write_text("context_chars = 10\n")
    with pytest.raises(ConfigError, match="context_chars"):
        load_config(f, environ={})


def test_session_file_naming_another_id_is_refused(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.save(Session(id="bbbbbb", cwd="/w", model="m"))
    (store.dir / "aaaaaa.json").write_text((store.dir / "bbbbbb.json").read_text())
    with pytest.raises(ValueError, match="different session id"):
        store.load("aaaaaa")
