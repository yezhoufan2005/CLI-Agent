"""Shell command execution with timeout, process-group kill, bounded output,
and a scrubbed environment so the model's commands cannot read our credentials."""

from __future__ import annotations

import contextlib
import ctypes
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

from cli_agent.tools.base import Effect, ToolContext, ToolError, ToolResult, require_str, truncate

MAX_TIMEOUT = 600.0
MAX_SPOOL_BYTES = 50_000_000
POLL_SECONDS = 0.1
# Environment variables that look like credentials are never passed to commands.
# Best-effort name denylist: commands run as the user and can still read files the
# user can read (see README "Permissions").
_SECRET_ENV = re.compile(
    r"(TOKEN|SECRET|PASSW|_PASS$|API_?KEY|ACCESS_?KEY|CREDENTIAL|PRIVATE_KEY|_AUTH|AUTH_|"
    r"COOKIE|SESSION_?KEY|DATABASE_URL|_DSN$|SSH_AUTH_SOCK|GPG_AGENT)",
    re.I,
)


def scrubbed_env(base: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in base.items() if not _SECRET_ENV.search(k)}
    # Commands run non-interactively: avoid pagers and prompts that would hang.
    env.update({"PAGER": "cat", "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"})
    return env


class RunShell:
    name = "run_shell"
    effect: Effect = "execute"
    description = (
        "Run a bash command in the workspace root and return its exit code and combined "
        "stdout/stderr. Commands are non-interactive (stdin is empty). Use for builds, tests, "
        "git, and other CLI tools. Long output is truncated in the middle. Processes the "
        "command leaves running in the background are killed when it finishes, so start "
        "and use a server within a single command."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Bash command line."},
            "timeout": {
                "type": "number",
                "description": f"Seconds before the command is killed (max {int(MAX_TIMEOUT)}).",
            },
        },
        "required": ["command"],
    }

    def summarize(self, args: dict[str, Any]) -> str:
        return f"$ {args.get('command')}"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = require_str(args, "command")
        timeout = args.get("timeout", ctx.shell_timeout)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ToolError("timeout must be a positive number")
        timeout = min(float(timeout), MAX_TIMEOUT)
        env = scrubbed_env(ctx.env or dict(os.environ))
        if ctx.checkpoints is not None:
            ctx.checkpoints.note_shell()
        _enable_subreaper()
        before = _child_pids()
        # Spool output to a temp file so a chatty command can't exhaust memory via pipes.
        with tempfile.TemporaryFile() as out:
            try:
                argv = ["bash", "-c", command]
                if ctx.sandbox is not None:
                    argv = ctx.sandbox.wrap(argv)
                proc = subprocess.Popen(
                    argv,
                    cwd=ctx.root,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as err:
                raise ToolError(f"failed to start bash: {err}") from err
            try:
                code, stopped = _wait(proc, out.fileno(), timeout)
            except KeyboardInterrupt:
                _kill_group(proc)
                _reap_orphans(before)
                raise
            # Whatever the command left behind (`&` jobs, setsid daemons) goes too.
            _kill_group(proc)
            _reap_orphans(before)
            raw = _read_head_tail(out, 2_000_000)
        text = truncate(raw.decode("utf-8", errors="replace"))
        if stopped == "timeout":
            return ToolResult(
                f"{text}\n[command timed out after {timeout:g}s and was killed]", True
            )
        if stopped == "output":
            return ToolResult(
                f"{text}\n[command produced more than {MAX_SPOOL_BYTES // 1_000_000} MB of "
                "output and was killed; redirect output to a file or filter it]",
                True,
            )
        header = f"exit code: {code}"
        return ToolResult(f"{header}\n{text}" if text else header, is_error=code != 0)


def _read_head_tail(fh: Any, half: int) -> bytes:
    """The first and last `half` bytes; errors usually sit at the end of output."""
    size = fh.seek(0, os.SEEK_END)
    fh.seek(0)
    if size <= 2 * half:
        return bytes(fh.read())
    head = fh.read(half)
    fh.seek(size - half)
    marker = f"\n\n[... {size - 2 * half} bytes omitted ...]\n\n".encode()
    return bytes(head + marker + fh.read())


def _wait(proc: subprocess.Popen[bytes], out_fd: int, timeout: float) -> tuple[int, str]:
    """Wait for exit, killing on timeout or runaway output. Returns (code, reason)."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        try:
            return proc.wait(timeout=max(min(POLL_SECONDS, remaining), 0.001)), ""
        except subprocess.TimeoutExpired:
            pass
        if os.fstat(out_fd).st_size > MAX_SPOOL_BYTES:
            reason = "output"
        elif time.monotonic() >= deadline:
            reason = "timeout"
        else:
            continue
        _kill_group(proc)
        return proc.wait(), reason


_PR_SET_CHILD_SUBREAPER = 36
_subreaper = False


def _enable_subreaper() -> None:
    """On Linux, become a subreaper so orphaned descendants of a command (e.g.
    after `setsid`) are re-parented to us and can be found and killed."""
    global _subreaper
    if _subreaper or not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        _subreaper = int(libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)) == 0
    except (OSError, AttributeError):
        _subreaper = False


def _child_pids() -> set[int]:
    """PIDs whose parent is this process (Linux /proc; empty elsewhere)."""
    if not _subreaper:
        return set()
    me = os.getpid()
    found = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as fh:
                stat = fh.read().decode("ascii", "replace")
        except OSError:
            continue
        # Format: pid (comm) state ppid ...; comm may contain spaces or parens.
        fields = stat[stat.rfind(")") + 2 :].split()
        if len(fields) > 1 and fields[1] == str(me):
            found.add(int(entry))
    return found


def _reap_orphans(before: set[int]) -> None:
    """Kill and reap descendants that escaped the process group and were
    re-parented to us. Killing one can orphan its own children, so repeat
    until a scan finds none (bounded in time, not in depth)."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        orphans = _child_pids() - before
        if not orphans:
            return
        for pid in orphans:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        for pid in orphans:
            with contextlib.suppress(OSError):
                os.waitpid(pid, 0)


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    # killpg can fail with ESRCH once the group is gone, and on macOS with EPERM
    # when only zombies remain in it; either way there is nothing left to kill.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
