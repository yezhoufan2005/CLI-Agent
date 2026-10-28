"""Drive the interactive REPL through a pseudo-terminal, as a user would."""

import json
import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")


class Term:
    def __init__(self, tmp_path: Path, script: list, *args: str):  # type: ignore[type-arg]
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        (tmp_path / "config.toml").write_text('provider = "fake"\n')
        (tmp_path / "script.json").write_text(json.dumps(script))
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path / "home"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "CLI_AGENT_CONFIG": str(tmp_path / "config.toml"),
            "CLI_AGENT_FAKE_SCRIPT": str(tmp_path / "script.json"),
            "TERM": "dumb",
        }
        self.master, slave = pty.openpty()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "cli_agent", "-C", str(work), *args],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            close_fds=True,
        )
        os.close(slave)
        self.buf = ""

    def expect(self, text: str, timeout: float = 15) -> None:
        deadline = time.monotonic() + timeout
        while text not in self.buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"did not see {text!r}; output so far:\n{self.buf}")
            ready, _, _ = select.select([self.master], [], [], remaining)
            if ready:
                try:
                    chunk = os.read(self.master, 4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    raise AssertionError(f"EOF before {text!r}; output:\n{self.buf}")
                self.buf += chunk.decode("utf-8", "replace")

    def send(self, line: str) -> None:
        os.write(self.master, (line + "\n").encode())

    def close(self) -> int:
        try:
            return self.proc.wait(timeout=15)
        finally:
            os.close(self.master)


def test_repl_conversation_with_approval(tmp_path: Path) -> None:
    script = [
        {"text": "first answer"},
        {"tool_calls": [{"name": "write_file", "input": {"path": "x.txt", "content": "hi"}}]},
        {"text": "wrote it"},
    ]
    t = Term(tmp_path, script)
    t.expect("Type /exit")
    t.send("hello")
    t.expect("first answer")
    t.send("/nonsense")
    t.expect("unknown command /nonsense")
    t.send("make a file")
    t.expect("[y]es")
    t.send("y")
    t.expect("wrote it")
    t.send("/exit")
    assert t.close() == 0
    assert (tmp_path / "work/x.txt").read_text() == "hi"


def test_repl_decline_and_mode_switch(tmp_path: Path) -> None:
    script = [
        {"tool_calls": [{"name": "run_shell", "input": {"command": "touch nope"}}]},
        {"text": "ok, not running it"},
        {"tool_calls": [{"name": "write_file", "input": {"path": "y.txt", "content": "y"}}]},
        {"text": "done"},
    ]
    t = Term(tmp_path, script)
    t.expect("Type /exit")
    t.send("run something")
    t.expect("[y]es")
    t.send("n")
    t.expect("ok, not running it")
    t.send("/mode auto-edit")
    t.expect("mode is now auto-edit")
    t.send("write y")
    t.expect("done")
    os.write(t.master, b"\x04")  # Ctrl-D exits
    assert t.close() == 0
    assert not (tmp_path / "work/nope").exists()
    assert (tmp_path / "work/y.txt").read_text() == "y"
