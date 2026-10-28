"""Terminal front-ends: human-readable UI and a JSON-lines event stream."""

from __future__ import annotations

import json
import re
import sys
from typing import IO, Any

from cli_agent.agent import Approval
from cli_agent.messages import ToolUseBlock
from cli_agent.tools import ToolResult

PREVIEW_LINES = 8
# C0/C1 control characters except tab and newline. Model-supplied text is shown
# with these escaped, so escape sequences or carriage returns cannot make an
# approval prompt display something other than what will run.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def escape_controls(text: str, *, keep_newlines: bool = True) -> str:
    def esc(m: re.Match[str]) -> str:
        return repr(m.group())[1:-1]

    out = _CONTROL.sub(esc, text)
    return out if keep_newlines else out.replace("\n", "\\n")


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
        self._streamed = False
        self._at_line_start = True

    streams = True

    def on_text_delta(self, text: str) -> None:
        self._streamed = True
        self._at_line_start = text.endswith("\n")
        self.out.write(escape_controls(text))
        self.out.flush()

    def on_text(self, text: str) -> None:
        if self._streamed:
            self._end_line()
            self._streamed = False
        else:
            print(escape_controls(text), file=self.out, flush=True)

    def _end_line(self) -> None:
        """Finish a partially streamed line before printing anything else."""
        if not self._at_line_start:
            self.out.write("\n")
            self.out.flush()
            self._at_line_start = True

    def on_tool_start(self, call: ToolUseBlock, summary: str) -> None:
        line = escape_controls(summary, keep_newlines=False)
        print(f"{self._dim}● {line}{self._reset}", file=self.err, flush=True)

    def on_tool_end(self, call: ToolUseBlock, result: ToolResult) -> None:
        if result.is_error:
            text = escape_controls(_preview(result.content, 4))
            print(f"{self._red}  ✗ {text}{self._reset}", file=self.err)
        elif self.verbose:
            print(
                f"{self._dim}{escape_controls(_preview(result.content))}{self._reset}",
                file=self.err,
            )

    def on_notice(self, message: str) -> None:
        self._end_line()
        line = escape_controls(message, keep_newlines=False)
        print(f"{self._yellow}! {line}{self._reset}", file=self.err, flush=True)

    def _show_call(self, call: ToolUseBlock, full: bool) -> None:
        if call.name == "write_file" and isinstance(call.input.get("content"), str):
            body = call.input["content"]
            print(escape_controls(body if full else _preview(body, 20)), file=self.err)
        elif call.name == "edit_file":
            old, new = str(call.input.get("old_string")), str(call.input.get("new_string"))
            if not full:
                old, new = _preview(old, 10), _preview(new, 10)
            print(f"{self._red}- {escape_controls(old)}{self._reset}", file=self.err)
            print(f"+ {escape_controls(new)}", file=self.err)

    def approve(self, call: ToolUseBlock, summary: str) -> Approval:
        line = escape_controls(summary, keep_newlines=False)
        if not self.interactive:
            self.on_notice(f"not approved (non-interactive): {line}")
            return "no"
        print(f"{self._yellow}? approve: {line}{self._reset}", file=self.err)
        self._show_call(call, full=False)
        while True:
            print(
                "  [y]es / [n]o / [a]lways for this session / [v]iew full: ",
                end="",
                file=self.err,
                flush=True,
            )
            reply = self.inp.readline()
            if not reply:
                return "no"
            answer = reply.strip().lower()
            if answer in ("y", "yes"):
                return "yes"
            if answer in ("n", "no", ""):
                return "no"
            if answer in ("a", "always"):
                return "always"
            if answer in ("v", "view"):
                self._show_call(call, full=True)


class JsonUI:
    """Machine-readable event stream, one JSON object per line on stdout."""

    def __init__(self, out: IO[str] | None = None):
        self.out = out or sys.stdout

    def emit(self, event: str, **data: Any) -> None:
        self.out.write(json.dumps({"event": event, **data}, ensure_ascii=False) + "\n")
        self.out.flush()

    streams = False

    def on_text_delta(self, text: str) -> None:
        pass

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
