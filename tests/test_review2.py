"""Regressions for the second independent review (see docs/progress.md)."""

import io
import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest

from cli_agent import log
from cli_agent.messages import Message, TextBlock, ToolUseBlock
from cli_agent.providers.anthropic import AnthropicProvider
from cli_agent.providers.base import ProviderError
from cli_agent.providers.openai import OpenAIProvider
from cli_agent.session import Session, SessionStore
from cli_agent.tools import ToolContext, ToolError
from cli_agent.tools.shell import RunShell
from cli_agent.ui import JsonUI, TerminalUI, escape_controls
from cli_agent.workspace import git_warning

MSGS = [Message("user", [TextBlock("hi")])]


def test_git_check_does_not_run_repo_fsmonitor(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\nenv > {marker}\n")
    hook.chmod(0o755)
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.fsmonitor", str(hook)], check=True)
    git_warning(tmp_path)
    assert not marker.exists()


@pytest.mark.parametrize(
    "body",
    [
        {"content": [{"type": "tool_use", "name": "x", "input": {}}]},  # no id
        {"content": "nope"},
        {"content": [{"type": "text", "text": "a"}], "usage": {"input_tokens": "lots"}},
        ["not", "an", "object"],
    ],
)
def test_anthropic_malformed_response_is_provider_error(body: object) -> None:
    p = AnthropicProvider(
        model="m",
        base_url="https://t",
        api_key="k",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    )
    with pytest.raises(ProviderError, match=r"malformed|not"):
        p.complete(system="", messages=MSGS, tools=[], max_tokens=5)


@pytest.mark.parametrize(
    "body",
    [
        {"choices": ["oops"]},
        ["x"],
        {"choices": [{"message": "s"}]},
        {"choices": [{"message": {"tool_calls": {"a": 1}}}]},
        {"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": "12.0"}},
    ],
)
def test_openai_malformed_response_is_provider_error(body: object) -> None:
    p = OpenAIProvider(
        model="m",
        base_url="https://t",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    )
    with pytest.raises(ProviderError):
        p.complete(system="", messages=MSGS, tools=[], max_tokens=5)


def test_malformed_stream_error_does_not_quote_payload() -> None:
    body = b"data: {MODEL-SECRET-TEXT\n\n"
    p = AnthropicProvider(
        model="m",
        base_url="https://t",
        api_key="k",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)),
    )
    with pytest.raises(ProviderError) as info:
        p.complete(system="", messages=MSGS, tools=[], max_tokens=5, on_text=lambda t: None)
    assert "SECRET" not in str(info.value)


def test_lone_surrogates_survive_save_and_output(tmp_path: Path) -> None:
    bad = json.loads('"x\\udc80y"')
    s = Session(id="s", cwd="/w", model="m")
    s.messages = [
        Message("user", [TextBlock(bad)]),
        Message("assistant", [ToolUseBlock("t", "n", {"a": bad})]),
    ]
    SessionStore(tmp_path).save(s)
    assert SessionStore(tmp_path).load("s").messages[0].content[0].text == bad  # type: ignore[union-attr]
    out = io.StringIO()
    JsonUI(out=out).on_text(bad)
    assert json.loads(out.getvalue())["text"] == bad
    term = io.BytesIO()
    ui = TerminalUI(
        interactive=False, out=io.TextIOWrapper(term, encoding="utf-8"), err=io.StringIO()
    )
    ui.on_text_delta(bad)
    ui.on_text(bad)


def test_bidi_and_zero_width_escaped() -> None:
    assert escape_controls("a‮b⁦c​d") == "a\\u202eb\\u2066c\\u200bd"


@pytest.mark.parametrize("t", [float("nan"), float("inf"), float("-inf")])
def test_shell_rejects_non_finite_timeout(tmp_path: Path, t: float) -> None:
    with pytest.raises(ToolError):
        RunShell().run({"command": "true", "timeout": t}, ToolContext(root=tmp_path))


def test_shell_keeps_tail_of_huge_output(tmp_path: Path) -> None:
    cmd = "head -c 6000000 /dev/zero | tr '\\0' a; echo; echo FINAL-ERROR-LINE"
    res = RunShell().run({"command": cmd, "timeout": 60}, ToolContext(root=tmp_path))
    assert "FINAL-ERROR-LINE" in res.content


def test_streamed_partial_text_marked_on_retry() -> None:
    out = io.StringIO()
    ui = TerminalUI(interactive=False, out=out, err=io.StringIO())
    ui.on_text_delta("Hello PART")
    ui.on_notice("model call failed; retry 1")
    ui.on_text_delta("Hello world")
    ui.on_text("Hello world")
    assert out.getvalue() == "Hello PART\n[partial response discarded]\nHello world\n"


def test_debug_log_rejects_fifo_and_keeps_mode(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    # Must fail fast (ENXIO with no reader, or "not a regular file"), never hang.
    with pytest.raises(OSError):
        log.enable(fifo)
