"""The agent loop: model call -> permission check -> tool execution -> repeat."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from cli_agent.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock
from cli_agent.permissions import PermissionPolicy
from cli_agent.providers.base import Provider, ProviderError, RetryPolicy, with_retries
from cli_agent.session import Session
from cli_agent.tools import Tool, ToolContext, ToolError, ToolResult, spec_of

Approval = Literal["yes", "no", "always"]

# Rough context budget in characters (~4 chars/token). When the history grows
# past it, the oldest tool results are elided; the newest turns are kept intact.
DEFAULT_CONTEXT_CHARS = 400_000
ELIDED = "[older tool output elided to save context]"


class UI(Protocol):
    def on_text(self, text: str) -> None: ...
    def on_tool_start(self, call: ToolUseBlock, summary: str) -> None: ...
    def on_tool_end(self, call: ToolUseBlock, result: ToolResult) -> None: ...
    def on_notice(self, message: str) -> None: ...
    def approve(self, call: ToolUseBlock, summary: str) -> Approval: ...


@dataclass
class RunResult:
    status: Literal["done", "max_turns", "max_tokens", "refusal", "error", "interrupted"]
    final_text: str = ""
    turns: int = 0
    error: str = ""


@dataclass
class Agent:
    provider: Provider
    tools: list[Tool]
    policy: PermissionPolicy
    ctx: ToolContext
    ui: UI
    system: str
    max_turns: int = 50
    max_tokens: int = 8192
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    context_chars: int = DEFAULT_CONTEXT_CHARS
    save: Callable[[Session], None] = lambda s: None
    sleep: Callable[[float], None] | None = None

    def __post_init__(self) -> None:
        self._by_name = {t.name: t for t in self.tools}

    def run(self, session: Session, prompt: str) -> RunResult:
        """Process one user prompt until the model stops calling tools."""
        if session.repair():
            self.ui.on_notice("previous run was interrupted; recorded unfinished tool calls")
        session.add_user_text(prompt)
        self.save(session)
        turns = 0
        last_text = ""
        try:
            while True:
                if turns >= self.max_turns:
                    return RunResult("max_turns", last_text, turns, f"stopped after {turns} turns")
                turns += 1
                compact_history(session.messages, self.context_chars)
                try:
                    completion = self._complete(session)
                except ProviderError as err:
                    return RunResult("error", last_text, turns, str(err))
                session.usage.add(completion.usage)
                msg = completion.message
                if not msg.content:
                    # An empty assistant message is invalid in history; record a placeholder.
                    msg.content.append(TextBlock(text="(no response)"))
                session.messages.append(msg)
                self.save(session)
                text = msg.text()
                if text:
                    last_text = text
                    self.ui.on_text(text)
                calls = msg.tool_uses()
                if not calls:
                    if completion.stop_reason == "max_tokens":
                        return RunResult("max_tokens", last_text, turns, "response hit max_tokens")
                    if completion.stop_reason == "refusal":
                        return RunResult("refusal", last_text, turns, "model refused")
                    return RunResult("done", last_text, turns)
                results = [self._execute(call) for call in calls]
                session.messages.append(Message(role="user", content=list(results)))
                self.save(session)
        except KeyboardInterrupt:
            session.repair()
            self.save(session)
            return RunResult("interrupted", last_text, turns, "interrupted by user")

    def _complete(self, session: Session):  # type: ignore[no-untyped-def]
        specs = [spec_of(t) for t in self.tools]

        def call():  # type: ignore[no-untyped-def]
            return self.provider.complete(
                system=self.system,
                messages=session.messages,
                tools=specs,
                max_tokens=self.max_tokens,
            )

        def on_retry(attempt: int, err: ProviderError, wait: float) -> None:
            self.ui.on_notice(f"model call failed ({err}); retry {attempt} in {wait:.1f}s")

        kwargs: dict[str, Any] = {"on_retry": on_retry}
        if self.sleep is not None:
            kwargs["sleep"] = self.sleep
        return with_retries(call, self.retry, **kwargs)

    def _execute(self, call: ToolUseBlock) -> ToolResultBlock:
        tool = self._by_name.get(call.name)
        if tool is None:
            known = ", ".join(sorted(self._by_name))
            return ToolResultBlock(call.id, f"unknown tool {call.name!r}; available: {known}", True)
        try:
            summary = tool.summarize(call.input)
        except Exception:  # noqa: BLE001 - summaries must never break the loop
            summary = f"{call.name} {json.dumps(call.input)[:200]}"
        verdict = self.policy.check(call.name, tool.effect, call.input)
        if verdict.decision == "deny":
            self.ui.on_notice(f"denied: {summary} ({verdict.reason})")
            return ToolResultBlock(call.id, f"permission denied: {verdict.reason}", True)
        if verdict.decision == "ask":
            answer = self.ui.approve(call, summary)
            if answer == "no":
                return ToolResultBlock(
                    call.id,
                    "the user declined this tool call; ask them how to proceed or try another way",
                    True,
                )
            if answer == "always":
                rule = self.policy.remember(call.name, call.input)
                self.ui.on_notice(f"added allow rule {rule!r} for this session")
        self.ui.on_tool_start(call, summary)
        try:
            result = tool.run(call.input, self.ctx)
        except ToolError as err:
            result = ToolResult(str(err), is_error=True)
        except KeyboardInterrupt:
            raise
        except Exception as err:  # noqa: BLE001 - a tool bug must not kill the session
            result = ToolResult(f"internal tool error: {type(err).__name__}: {err}", is_error=True)
        self.ui.on_tool_end(call, result)
        return ToolResultBlock(call.id, result.content or "(no output)", result.is_error)


def history_chars(messages: list[Message]) -> int:
    total = 0
    for m in messages:
        for b in m.content:
            if isinstance(b, TextBlock):
                total += len(b.text)
            elif isinstance(b, ToolResultBlock):
                total += len(b.content)
            else:
                total += len(json.dumps(b.input))
    return total


def compact_history(messages: list[Message], budget: int, keep_recent: int = 6) -> int:
    """Elide the oldest tool results until the history fits `budget`.

    The last `keep_recent` messages are never touched so the model keeps the
    details it is currently working with. Returns the number of blocks elided.
    """
    elided = 0
    if history_chars(messages) <= budget:
        return 0
    for m in messages[: max(len(messages) - keep_recent, 0)]:
        for b in m.content:
            if isinstance(b, ToolResultBlock) and b.content != ELIDED and len(b.content) > 200:
                b.content = ELIDED
                elided += 1
                if history_chars(messages) <= budget:
                    return elided
    return elided
