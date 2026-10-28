"""Shell command execution with timeout, process-group kill, bounded output,
and a scrubbed environment so the model's commands cannot read our credentials."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
from typing import Any

from cli_agent.tools.base import Effect, ToolContext, ToolError, ToolResult, require_str, truncate

MAX_TIMEOUT = 600.0
# Environment variables that look like credentials are never passed to commands.
_SECRET_ENV = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIAL|PRIVATE_KEY)", re.I)


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
        "git, and other CLI tools. Long output is truncated in the middle."
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
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ToolError("timeout must be a positive number")
        timeout = min(float(timeout), MAX_TIMEOUT)
        env = scrubbed_env(ctx.env or dict(os.environ))
        # Spool output to a temp file so a chatty command can't exhaust memory via pipes.
        with tempfile.TemporaryFile() as out:
            try:
                proc = subprocess.Popen(
                    ["bash", "-c", command],
                    cwd=ctx.root,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as err:
                raise ToolError(f"failed to start bash: {err}") from err
            timed_out = False
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(proc)
                code = proc.wait()
            except KeyboardInterrupt:
                _kill_group(proc)
                proc.wait()
                raise
            out.seek(0)
            raw = out.read(4_000_000)
        text = truncate(raw.decode("utf-8", errors="replace"))
        if timed_out:
            return ToolResult(
                f"{text}\n[command timed out after {timeout:g}s and was killed]", True
            )
        header = f"exit code: {code}"
        return ToolResult(f"{header}\n{text}" if text else header, is_error=code != 0)


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
