from pathlib import Path

import pytest

from cli_agent.agent import ELIDED, Agent, compact_history
from cli_agent.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock
from cli_agent.permissions import PermissionPolicy
from cli_agent.providers.base import ProviderError, RetryPolicy
from cli_agent.providers.fake import FakeProvider
from cli_agent.session import INTERRUPTED, Session
from cli_agent.tools import ToolContext, ToolResult, default_tools


class RecordingUI:
    def __init__(self, answers: list[str] | None = None):
        self.answers = list(answers or [])
        self.events: list[tuple[str, str]] = []

    def on_text(self, text: str) -> None:
        self.events.append(("text", text))

    def on_tool_start(self, call: ToolUseBlock, summary: str) -> None:
        self.events.append(("start", summary))

    def on_tool_end(self, call: ToolUseBlock, result: ToolResult) -> None:
        self.events.append(("end", result.content))

    def on_notice(self, message: str) -> None:
        self.events.append(("notice", message))

    def approve(self, call: ToolUseBlock, summary: str):  # type: ignore[no-untyped-def]
        self.events.append(("approve", summary))
        return self.answers.pop(0) if self.answers else "no"


def make_agent(tmp_path: Path, script: list, mode: str = "auto", ui=None, **kw):  # type: ignore[no-untyped-def]
    provider = FakeProvider(script=script)
    agent = Agent(
        provider=provider,
        tools=default_tools(),
        policy=PermissionPolicy(mode=mode),  # type: ignore[arg-type]
        ctx=ToolContext(root=tmp_path),
        ui=ui or RecordingUI(),
        system="sys",
        retry=RetryPolicy(max_attempts=3, base_delay=0),
        sleep=lambda s: None,
        **kw,
    )
    return agent, provider


def new_session(tmp_path: Path) -> Session:
    return Session(id="s1", cwd=str(tmp_path), model="fake")


def test_tool_loop_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n")
    script = [
        {"text": "reading", "tool_calls": [{"name": "read_file", "input": {"path": "a.txt"}}]},
        {
            "tool_calls": [
                {
                    "name": "edit_file",
                    "input": {"path": "a.txt", "old_string": "hello", "new_string": "bye"},
                }
            ]
        },
        {"text": "done"},
    ]
    agent, provider = make_agent(tmp_path, script)
    session = new_session(tmp_path)
    result = agent.run(session, "change it")
    assert result.status == "done" and result.final_text == "done" and result.turns == 3
    assert (tmp_path / "a.txt").read_text() == "bye\n"
    # The second request carried the first tool result back to the model.
    second = provider.requests[1].messages
    assert isinstance(second[-1].content[0], ToolResultBlock)
    assert "hello" in second[-1].content[0].content
    roles = [m.role for m in session.messages]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]
    assert session.usage.input_tokens == 30


def test_denied_call_reported_to_model(tmp_path: Path) -> None:
    script = [
        {"tool_calls": [{"name": "run_shell", "input": {"command": "touch x"}}]},
        {"text": "ok"},
    ]
    ui = RecordingUI(answers=["no"])
    agent, provider = make_agent(tmp_path, script, mode="ask", ui=ui)
    agent.run(new_session(tmp_path), "go")
    assert not (tmp_path / "x").exists()
    result = provider.requests[1].messages[-1].content[0]
    assert isinstance(result, ToolResultBlock) and result.is_error
    assert "declined" in result.content


def test_always_approval_sticks(tmp_path: Path) -> None:
    script = [
        {"tool_calls": [{"name": "run_shell", "input": {"command": "echo hi"}}]},
        {"tool_calls": [{"name": "run_shell", "input": {"command": "echo hi"}}]},
        {"text": "ok"},
    ]
    ui = RecordingUI(answers=["always"])
    agent, _ = make_agent(tmp_path, script, mode="ask", ui=ui)
    agent.run(new_session(tmp_path), "go")
    assert [e for e in ui.events if e[0] == "approve"] == [("approve", "$ echo hi")]
    assert sum(1 for e in ui.events if e[0] == "start") == 2


def test_read_only_mode_blocks_write(tmp_path: Path) -> None:
    script = [
        {"tool_calls": [{"name": "write_file", "input": {"path": "x", "content": "y"}}]},
        {"text": "ok"},
    ]
    agent, provider = make_agent(tmp_path, script, mode="read-only")
    agent.run(new_session(tmp_path), "go")
    assert not (tmp_path / "x").exists()
    assert "permission denied" in provider.requests[1].messages[-1].content[0].content  # type: ignore[union-attr]


def test_unknown_tool_and_bad_args_are_recoverable(tmp_path: Path) -> None:
    script = [
        {
            "tool_calls": [
                {"name": "nope", "input": {}},
                {"name": "read_file", "input": {"path": 7}},
            ]
        },
        {"text": "recovered"},
    ]
    agent, provider = make_agent(tmp_path, script)
    result = agent.run(new_session(tmp_path), "go")
    assert result.status == "done"
    results = provider.requests[1].messages[-1].content
    assert len(results) == 2 and all(isinstance(r, ToolResultBlock) and r.is_error for r in results)
    assert "unknown tool" in results[0].content  # type: ignore[union-attr]


def test_retry_then_success(tmp_path: Path) -> None:
    script = [ProviderError("overloaded", retryable=True), {"text": "fine"}]
    ui = RecordingUI()
    agent, _ = make_agent(tmp_path, script, ui=ui)
    assert agent.run(new_session(tmp_path), "go").status == "done"
    assert any("retry 1" in m for k, m in ui.events if k == "notice")


def test_non_retryable_error_stops(tmp_path: Path) -> None:
    script = [ProviderError("HTTP 401: bad key", retryable=False), {"text": "never"}]
    agent, _ = make_agent(tmp_path, script)
    result = agent.run(new_session(tmp_path), "go")
    assert result.status == "error" and "401" in result.error


def test_retries_exhausted(tmp_path: Path) -> None:
    script = [ProviderError("busy", retryable=True)] * 3
    agent, _ = make_agent(tmp_path, script)
    assert agent.run(new_session(tmp_path), "go").status == "error"


def test_max_turns(tmp_path: Path) -> None:
    script = [{"tool_calls": [{"name": "list_files", "input": {}}]}] * 5
    agent, _ = make_agent(tmp_path, script, max_turns=2)
    result = agent.run(new_session(tmp_path), "go")
    assert result.status == "max_turns" and result.turns == 2


def test_max_tokens_reported(tmp_path: Path) -> None:
    agent, _ = make_agent(tmp_path, [{"text": "trunc", "stop_reason": "max_tokens"}])
    assert agent.run(new_session(tmp_path), "go").status == "max_tokens"


def test_interrupt_during_tool_leaves_valid_history(tmp_path: Path) -> None:
    script = [{"tool_calls": [{"name": "run_shell", "input": {"command": "sleep 1"}}]}]
    agent, _ = make_agent(tmp_path, script)

    def boom(args, ctx):  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt

    agent._by_name["run_shell"].run = boom  # type: ignore[method-assign]
    saved: list[int] = []
    agent.save = lambda s: saved.append(len(s.messages))
    session = new_session(tmp_path)
    result = agent.run(session, "go")
    assert result.status == "interrupted"
    last = session.messages[-1]
    assert last.role == "user" and isinstance(last.content[0], ToolResultBlock)
    assert last.content[0].content == INTERRUPTED
    assert saved[-1] == len(session.messages)


def test_session_repair_on_resume(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    session.messages = [
        Message("user", [TextBlock("hi")]),
        Message("assistant", [ToolUseBlock("t1", "list_files", {})]),
    ]
    agent, provider = make_agent(tmp_path, [{"text": "resumed"}])
    agent.run(session, "continue")
    msgs = provider.requests[0].messages
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    # Repaired tool result and the new prompt share one user message.
    kinds = [type(b).__name__ for b in msgs[-1].content]
    assert kinds == ["ToolResultBlock", "TextBlock"]


def test_compact_history_elides_oldest(tmp_path: Path) -> None:
    msgs = []
    for i in range(10):
        msgs.append(Message("assistant", [ToolUseBlock(f"t{i}", "read_file", {"path": "x"})]))
        msgs.append(Message("user", [ToolResultBlock(f"t{i}", "x" * 1000)]))
    n = compact_history(msgs, budget=6000, keep_recent=4)
    assert n > 0
    assert msgs[1].content[0].content == ELIDED  # type: ignore[union-attr]
    assert msgs[-1].content[0].content == "x" * 1000  # type: ignore[union-attr]
    assert compact_history(msgs, budget=10**9) == 0


def test_tool_crash_is_contained(tmp_path: Path) -> None:
    script = [{"tool_calls": [{"name": "list_files", "input": {}}]}, {"text": "ok"}]
    agent, provider = make_agent(tmp_path, script)

    def crash(args, ctx):  # type: ignore[no-untyped-def]
        raise RuntimeError("bug")

    agent._by_name["list_files"].run = crash  # type: ignore[method-assign]
    assert agent.run(new_session(tmp_path), "go").status == "done"
    res = provider.requests[1].messages[-1].content[0]
    assert "internal tool error: RuntimeError" in res.content  # type: ignore[union-attr]


@pytest.mark.parametrize("bad", [[], [{"text": ""}]])
def test_empty_response_does_not_corrupt_history(tmp_path: Path, bad: list) -> None:  # type: ignore[type-arg]
    script = bad or [{}]
    agent, _ = make_agent(tmp_path, script)
    session = new_session(tmp_path)
    agent.run(session, "go")
    assert session.messages[-1].role == "assistant" and session.messages[-1].content


def test_incomplete_tool_call_not_executed(tmp_path: Path) -> None:
    script = [
        {
            "tool_calls": [{"name": "write_file", "input": {}, "complete": False}],
            "stop_reason": "max_tokens",
        },
        {"text": "ok"},
    ]
    agent, provider = make_agent(tmp_path, script)
    assert agent.run(new_session(tmp_path), "go").status == "done"
    res = provider.requests[1].messages[-1].content[0]
    assert isinstance(res, ToolResultBlock) and res.is_error and "cut off" in res.content
