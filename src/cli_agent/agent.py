"""The agent loop: model call -> permission check -> tool execution -> repeat."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from cli_agent import log
from cli_agent.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock
from cli_agent.permissions import PermissionPolicy
from cli_agent.providers.base import Provider, ProviderError, RetryPolicy, with_retries
from cli_agent.session import Session
from cli_agent.tools import Tool, ToolContext, ToolError, ToolResult, spec_of

Approval = Literal["yes", "no", "always", "unattended"]
"""`unattended`: nobody can answer (no terminal), so the call is not run."""

# Rough context budget in characters (~4 chars/token). When the history grows
# past it, the oldest tool results are elided; the newest turns are kept intact.
DEFAULT_CONTEXT_CHARS = 400_000
ELIDED = "[older tool output elided to save context]"
_ELIDED_PREFIX = "[elided: "


def elided_marker(call: ToolUseBlock | None, size: int, note: str = "") -> str:
    """Name the call whose output was removed, so the model knows what it already
    did and does not loop re-running the same reads.

    `note` is the text the model wrote right after seeing the output. It is
    repeated inside the marker because some Anthropic-compatible backends drop
    assistant text that shares a message with tool_use blocks (observed with the
    development proxy; see docs/progress.md), while tool results always survive.
    """
    if call is None:
        return ELIDED
    args = json.dumps(call.input, ensure_ascii=False)
    if len(args) > 200:
        args = args[:200] + "…"
    note = " ".join(note.split())
    if len(note) > 500:
        note = note[:500] + "…"
    recorded = f" Your note after reading it: {note}" if note else ""
    return (
        f"{_ELIDED_PREFIX}output of {call.name} {args} ({size} chars) was removed to save "
        f"context.{recorded} Re-run it only if you need details you did not note.]"
    )


def _is_elided(text: str) -> bool:
    return text == ELIDED or text.startswith(_ELIDED_PREFIX)


class UI(Protocol):
    streams: bool
    """True if the UI wants text deltas via on_text_delta while the model writes."""

    def on_text_delta(self, text: str) -> None: ...
    def on_text(self, text: str) -> None:
        """Called once per assistant message with its full text."""
        ...

    def on_tool_start(self, call: ToolUseBlock, summary: str) -> None: ...
    def on_tool_end(self, call: ToolUseBlock, result: ToolResult) -> None: ...
    def on_notice(self, message: str) -> None: ...
    def approve(self, call: ToolUseBlock, summary: str) -> Approval: ...


@dataclass
class RunResult:
    status: Literal["done", "max_turns", "max_tokens", "budget", "refusal", "error", "interrupted"]
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
    max_session_tokens: int | None = None
    """Stop once the session's input+output tokens reach this (a cost budget)."""
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
        _drop_pending_continue(session)
        session.add_user_text(prompt)
        self.save(session)
        if self.ctx.checkpoints is not None:
            self.ctx.checkpoints.begin(prompt)
        turns = 0
        shrinks = 0
        continuations = 0
        continued = ""  # text of earlier replies in a run of max_tokens continuations
        last_text = ""
        try:
            while True:
                if turns >= self.max_turns:
                    return RunResult("max_turns", last_text, turns, f"stopped after {turns} turns")
                spent = session.usage.input_tokens + session.usage.output_tokens
                if self.max_session_tokens is not None and spent >= self.max_session_tokens:
                    return RunResult(
                        "budget",
                        last_text,
                        turns,
                        f"token budget reached ({spent} of {self.max_session_tokens})",
                    )
                turns += 1
                elided = compact_history(session.messages, self.context_chars)
                if elided:
                    log.event(
                        "compact", turn=turns, elided=elided, chars=history_chars(session.messages)
                    )
                started = time.monotonic()
                try:
                    completion = self._complete(session)
                except ProviderError as err:
                    if is_context_overflow(err) and shrinks < MAX_SHRINKS and self._shrink(session):
                        shrinks += 1
                        turns -= 1
                        continue
                    log.event(
                        "model_error", turn=turns, retryable=err.retryable, error=str(err)[:300]
                    )
                    return RunResult("error", last_text, turns, str(err))
                u = completion.usage
                log.event(
                    "model_call",
                    turn=turns,
                    secs=round(time.monotonic() - started, 3),
                    model=completion.model,
                    stop=completion.stop_reason,
                    input_tokens=u.input_tokens,
                    output_tokens=u.output_tokens,
                    cache_read=u.cache_read_tokens,
                    cache_write=u.cache_write_tokens,
                    tool_calls=len(completion.message.tool_uses()),
                )
                session.usage.add(completion.usage)
                msg = completion.message
                if not msg.content:
                    # An empty assistant message is invalid in history; record a placeholder.
                    msg.content.append(TextBlock(text="(no response)"))
                session.messages.append(msg)
                self.save(session)
                text = msg.text()
                if text:
                    last_text = continued + text
                    self.ui.on_text(text)
                calls = msg.tool_uses()
                if not calls:
                    if completion.stop_reason == "max_tokens":
                        # A reply cut off mid-thought is usually not the end of the
                        # task; ask the model to carry on, a bounded number of times.
                        if continuations < MAX_CONTINUATIONS:
                            continuations += 1
                            continued = last_text
                            log.event("continue_after_max_tokens", turn=turns)
                            session.add_user_text(CONTINUE_PROMPT)
                            self.save(session)
                            continue
                        return RunResult("max_tokens", last_text, turns, "response hit max_tokens")
                    if completion.stop_reason == "refusal":
                        return RunResult("refusal", last_text, turns, "model refused")
                    return RunResult("done", last_text, turns)
                continuations = 0
                continued = ""
                results = [self._execute(call) for call in calls]
                session.messages.append(Message(role="user", content=list(results)))
                self.save(session)
        except KeyboardInterrupt:
            session.repair()
            self.save(session)
            return RunResult("interrupted", last_text, turns, "interrupted by user")

    def _shrink(self, session: Session) -> bool:
        """After the API rejected the prompt as too long, compact to half the
        current size. Returns False if nothing more can be elided."""
        size = history_chars(session.messages)
        elided = compact_history(session.messages, size // 2, keep_recent=2)
        log.event("compact_overflow", elided=elided, chars=history_chars(session.messages))
        if elided:
            self.ui.on_notice(f"prompt too long for the model; elided {elided} old tool output(s)")
        return elided > 0

    def _complete(self, session: Session):  # type: ignore[no-untyped-def]
        specs = [spec_of(t) for t in self.tools]

        on_text = self.ui.on_text_delta if getattr(self.ui, "streams", False) else None

        def call():  # type: ignore[no-untyped-def]
            return self.provider.complete(
                system=self.system,
                messages=session.messages,
                tools=specs,
                max_tokens=self.max_tokens,
                on_text=on_text,
            )

        def on_retry(attempt: int, err: ProviderError, wait: float) -> None:
            log.event("model_retry", attempt=attempt, wait=round(wait, 2), error=str(err)[:300])
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
        if not call.complete:
            return ToolResultBlock(
                call.id,
                "the tool arguments were cut off (the response hit the output token limit) and "
                "could not be parsed; nothing was run. Retry with smaller input, e.g. write a "
                "large file in several edits.",
                True,
            )
        try:
            summary = tool.summarize(call.input)
        except Exception:  # noqa: BLE001 - summaries must never break the loop
            summary = f"{call.name} {json.dumps(call.input)[:200]}"
        verdict = self.policy.check(call.name, tool.effect, call.input)
        log.event("permission", tool=call.name, decision=verdict.decision, reason=verdict.reason)
        if verdict.decision == "deny":
            self.ui.on_notice(f"denied: {summary} ({verdict.reason})")
            return ToolResultBlock(call.id, f"permission denied: {verdict.reason}", True)
        if verdict.decision == "ask":
            answer = self.ui.approve(call, summary)
            hint = self.policy.allow_hint(call.name, call.input)
            if hint:
                self.ui.on_notice(hint)
            if answer == "no":
                return ToolResultBlock(
                    call.id,
                    "the user declined this tool call; ask them how to proceed or try another way"
                    + (f". {hint}" if hint else ""),
                    True,
                )
            if answer == "unattended":
                return ToolResultBlock(
                    call.id,
                    "not run: this call needs the user's approval, and this run is "
                    "non-interactive, so nobody can give it. Only calls the user's allow rules "
                    "or permission mode permit can run" + (f". {hint}" if hint else "."),
                    True,
                )
            if answer == "always":
                rule = self.policy.remember(call.name, call.input)
                self.ui.on_notice(f"added allow rule {rule!r} for this session")
        self.ui.on_tool_start(call, summary)
        started = time.monotonic()
        try:
            result = tool.run(call.input, self.ctx)
        except ToolError as err:
            result = ToolResult(str(err), is_error=True)
        except KeyboardInterrupt:
            raise
        except Exception as err:  # noqa: BLE001 - a tool bug must not kill the session
            result = ToolResult(f"internal tool error: {type(err).__name__}: {err}", is_error=True)
        log.event(
            "tool",
            tool=call.name,
            secs=round(time.monotonic() - started, 3),
            is_error=result.is_error,
            output_chars=len(result.content),
        )
        self.ui.on_tool_end(call, result)
        return ToolResultBlock(call.id, result.content or "(no output)", result.is_error)


_OVERFLOW_HINTS = (
    "prompt is too long",
    "context length",
    "context window",
    "maximum context",
    "too many tokens",
)


MAX_SHRINKS = 4


def _drop_pending_continue(session: Session) -> None:
    """A continue prompt left by an interrupted or failed run is stale: the user
    has moved on, and their new prompt must not be merged into it."""
    if not session.messages or session.messages[-1].role != "user":
        return
    last = session.messages[-1]
    if (
        last.content
        and isinstance(last.content[-1], TextBlock)
        and (last.content[-1].text == CONTINUE_PROMPT)
    ):
        last.content.pop()
        if not last.content:
            session.messages.pop()


MAX_CONTINUATIONS = 3
CONTINUE_PROMPT = (
    "[Your previous reply was cut off at the output token limit. Continue from where you "
    "stopped; keep replies shorter, and make large file changes in several smaller edits.]"
)


def is_context_overflow(err: ProviderError) -> bool:
    """True if the prompt itself is too long. An overflow caused by the requested
    output size (max_tokens / "in the completion") is not fixed by eliding history."""
    text = str(err).lower()
    if "completion" in text or "max_tokens" in text:
        return False
    return not err.retryable and any(h in text for h in _OVERFLOW_HINTS)


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
    calls = {u.id: u for m in messages for u in m.tool_uses()}
    for i, m in enumerate(messages[: max(len(messages) - keep_recent, 0)]):
        following = messages[i + 1] if i + 1 < len(messages) else None
        note = following.text() if following and following.role == "assistant" else ""
        for b in m.content:
            if (
                isinstance(b, ToolResultBlock)
                and not _is_elided(b.content)
                and len(b.content) > 400
            ):
                b.content = elided_marker(calls.get(b.tool_use_id), len(b.content), note)
                elided += 1
                if history_chars(messages) <= budget:
                    return elided
    return elided
