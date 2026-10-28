"""Terminal front-ends: human-readable UI and a JSON-lines event stream."""

from __future__ import annotations

import json
import sys
from typing import IO, Any

from cli_agent.agent import Approval
from cli_agent.messages import ToolUseBlock
from cli_agent.tools import ToolResult

PREVIEW_LINES = 8


def _preview(text: str, lines: int = PREVIEW_LINES) -> str:
    rows = text.splitlines()
    if len(rows) <= lines:
        return text
    return "\n".join(rows[:lines]) + f"\n… ({len(rows) - lines} more lines)"


class TerminalUI:
    """Model text to stdout; tool activity and notices to stderr."""

    def __init__(
        self,
        *,
        interactive: bool,
        out: IO[str] | None = None,
        err: IO[str] | None = None,
        inp: IO[str] | None = None,
        verbose: bool = False,
    ):
        self.interactive = interactive
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.inp = inp or sys.stdin
        self.verbose = verbose
        color = hasattr(self.err, "isatty") and self.err.isatty()
        self._dim = "\x1b[2m" if color else ""
        self._red = "\x1b[31m" if color else ""
        self._yellow = "\x1b[33m" if color else ""
        self._reset = "\x1b[0m" if color else ""

    def on_text(self, text: str) -> None:
        print(text, file=self.out, flush=True)

    def on_tool_start(self, call: ToolUseBlock, summary: str) -> None:
        print(f"{self._dim}● {summary}{self._reset}", file=self.err, flush=True)

    def on_tool_end(self, call: ToolUseBlock, result: ToolResult) -> None:
        if result.is_error:
            print(f"{self._red}  ✗ {_preview(result.content, 4)}{self._reset}", file=self.err)
        elif self.verbose:
            print(f"{self._dim}{_preview(result.content)}{self._reset}", file=self.err)

    def on_notice(self, message: str) -> None:
        print(f"{self._yellow}! {message}{self._reset}", file=self.err, flush=True)

    def approve(self, call: ToolUseBlock, summary: str) -> Approval:
        if not self.interactive:
            self.on_notice(f"not approved (non-interactive): {summary}")
            return "no"
        print(f"{self._yellow}? approve: {summary}{self._reset}", file=self.err)
        if call.name in ("write_file",) and isinstance(call.input.get("content"), str):
            print(_preview(call.input["content"], 20), file=self.err)
        elif call.name == "edit_file":
            print(
                f"{self._red}- {_preview(str(call.input.get('old_string')), 10)}{self._reset}",
                file=self.err,
            )
            print(f"+ {_preview(str(call.input.get('new_string')), 10)}", file=self.err)
        while True:
            print("  [y]es / [n]o / [a]lways for this session: ", end="", file=self.err, flush=True)
            line = self.inp.readline()
            if not line:
                return "no"
            answer = line.strip().lower()
            if answer in ("y", "yes"):
                return "yes"
            if answer in ("n", "no", ""):
                return "no"
            if answer in ("a", "always"):
                return "always"


class JsonUI:
    """Machine-readable event stream, one JSON object per line on stdout."""

    def __init__(self, out: IO[str] | None = None):
        self.out = out or sys.stdout

    def emit(self, event: str, **data: Any) -> None:
        self.out.write(json.dumps({"event": event, **data}, ensure_ascii=False) + "\n")
        self.out.flush()

    def on_text(self, text: str) -> None:
        self.emit("text", text=text)

    def on_tool_start(self, call: ToolUseBlock, summary: str) -> None:
        self.emit("tool_start", id=call.id, name=call.name, input=call.input, summary=summary)

    def on_tool_end(self, call: ToolUseBlock, result: ToolResult) -> None:
        self.emit(
            "tool_end", id=call.id, name=call.name, is_error=result.is_error, output=result.content
        )

    def on_notice(self, message: str) -> None:
        self.emit("notice", message=message)

    def approve(self, call: ToolUseBlock, summary: str) -> Approval:
        self.emit(
            "approval_denied", id=call.id, name=call.name, summary=summary, reason="non-interactive"
        )
        return "no"
