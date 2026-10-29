"""Terminal front-ends: human-readable UI and a JSON-lines event stream."""

from __future__ import annotations

import json
import os
import re
import select
import sys
import time
from typing import IO, Any

from cli_agent.agent import Approval
from cli_agent.messages import ToolUseBlock
from cli_agent.tools import ToolResult

PREVIEW_LINES = 8
DIFF_LINES = 30
# C0/C1 control characters except tab and newline. Model-supplied text is shown
# with these escaped, so escape sequences or carriage returns cannot make an
# approval prompt display something other than what will run.
# Also bidi overrides/isolates and zero-width characters, which can make text
# display in a different order than it runs, and lone surrogates, which cannot
# be written to a UTF-8 terminal at all.
_CONTROL = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff"
    r"\ud800-\udfff]"
)


MAX_TYPEAHEAD = 65536


TYPEAHEAD_QUIET = 0.2
TYPEAHEAD_MAX_WAIT = 2.0


def take_typeahead(inp: IO[str]) -> str:
    """Read and return input typed before a question was shown ("" if none).

    Otherwise a line typed ahead while the agent worked (`y`, or just Enter)
    would answer an approval prompt the user has not seen yet. The text is
    returned, not thrown away, so a typed-ahead command like `/mode read-only`
    can still be run by the REPL. Reading continues until input has been quiet
    for TYPEAHEAD_QUIET, so the rest of a paste that is still arriving is
    taken too; text past MAX_TYPEAHEAD is dropped. Finally the terminal's
    input queue is flushed, which also drops a partly typed line (a `y`
    without Enter) that would otherwise become the start of the answer.
    """
    try:
        if not inp.isatty():
            return ""
        fd = inp.fileno()
        data = b""
        deadline = time.monotonic() + TYPEAHEAD_MAX_WAIT
        while time.monotonic() < deadline and select.select([fd], [], [], TYPEAHEAD_QUIET)[0]:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            if len(data) < MAX_TYPEAHEAD:
                data += chunk
        try:
            import termios

            termios.tcflush(fd, termios.TCIFLUSH)
        except ImportError:
            pass
        return data[:MAX_TYPEAHEAD].decode("utf-8", "replace")
    except (OSError, ValueError, AttributeError):
        return ""


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
        self._green = "\x1b[32m" if color else ""
        self._yellow = "\x1b[33m" if color else ""
        self._reset = "\x1b[0m" if color else ""
        self._streamed = False
        self._at_line_start = True
        self.keep_typeahead = False
        """Set by the REPL: keep typed-ahead lines for its next prompt."""
        self.typeahead: list[str] = []

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
        if result.display and not result.is_error:
            self._print_diff(result.display)
        if result.is_error:
            text = escape_controls(_preview(result.content, 4))
            print(f"{self._red}  ✗ {text}{self._reset}", file=self.err)
        elif self.verbose:
            print(
                f"{self._dim}{escape_controls(_preview(result.content))}{self._reset}",
                file=self.err,
            )

    def _print_diff(self, diff: str) -> None:
        rows = diff.splitlines()
        shown = rows if self.verbose else rows[:DIFF_LINES]
        for row in shown:
            row = escape_controls(row, keep_newlines=False)
            if row.startswith(("+++", "---")):
                color = self._dim
            elif row.startswith("+"):
                color = self._green
            elif row.startswith("-"):
                color = self._red
            else:
                color = self._dim
            print(f"  {color}{row}{self._reset}", file=self.err)
        if len(shown) < len(rows):
            print(
                f"  {self._dim}… ({len(rows) - len(shown)} more diff lines; -v shows all)"
                f"{self._reset}",
                file=self.err,
            )

    def on_notice(self, message: str) -> None:
        if self._streamed:
            # A notice while streamed text is pending means that stream failed
            # (e.g. before a retry); its partial text is not part of the answer.
            self._end_line()
            print(f"{self._dim}[partial response discarded]{self._reset}", file=self.out)
            self._streamed = False
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
            return "unattended"
        print(f"{self._yellow}? approve: {line}{self._reset}", file=self.err)
        self._show_call(call, full=False)
        early = take_typeahead(self.inp)
        if early:
            lines = early.splitlines()
            shown = escape_controls(" ⏎ ".join(lines), keep_newlines=False)[:200]
            if self.keep_typeahead:
                self.typeahead.extend(lines)
                note = f"input typed before this question, kept for the next prompt: {shown}"
            else:
                note = f"ignored input typed before this question: {shown}"
            print(f"{self._dim}  ({note}){self._reset}", file=self.err)
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
                continue
            # Anything else (a stray line from a paste, a typo) is a refusal, so
            # leftover input can never cycle through the question until a `y`.
            print(f"  {self._dim}(not an answer; not running it){self._reset}", file=self.err)
            return "no"


class JsonUI:
    """Machine-readable event stream, one JSON object per line on stdout."""

    def __init__(self, out: IO[str] | None = None):
        self.out = out or sys.stdout

    def emit(self, event: str, **data: Any) -> None:
        line = json.dumps({"event": event, **data}, ensure_ascii=False)
        # Lone surrogates can't be written as UTF-8; emit them as JSON escapes.
        line = line.encode("utf-8", "backslashreplace").decode("utf-8")
        self.out.write(line + "\n")
        self.out.flush()

    streams = False

    def on_text_delta(self, text: str) -> None:
        pass

    def on_text(self, text: str) -> None:
        self.emit("text", text=text)

    def on_tool_start(self, call: ToolUseBlock, summary: str) -> None:
        self.emit("tool_start", id=call.id, name=call.name, input=call.input, summary=summary)

    def on_tool_end(self, call: ToolUseBlock, result: ToolResult) -> None:
        extra = {"diff": result.display} if result.display else {}
        self.emit(
            "tool_end",
            id=call.id,
            name=call.name,
            is_error=result.is_error,
            output=result.content,
            **extra,
        )

    def on_notice(self, message: str) -> None:
        self.emit("notice", message=message)

    def approve(self, call: ToolUseBlock, summary: str) -> Approval:
        self.emit(
            "approval_denied", id=call.id, name=call.name, summary=summary, reason="non-interactive"
        )
        return "unattended"
