"""Drive the interactive REPL through a pseudo-terminal, as a user would."""

import fcntl
import json
import os
import pty
import select
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")


def _take_tty() -> None:
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


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
            # Make the pty the controlling terminal so Ctrl-C sends SIGINT.
            preexec_fn=_take_tty,
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

    def send_eof(self) -> None:
        # readline prints the prompt before it takes the tty out of canonical mode; a
        # Ctrl-D typed in that window reaches readline as a NUL and is ignored. So
        # repeat it until the process exits (close() reports a REPL that never does).
        for _ in range(20):
            os.write(self.master, b"\x04")
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    return
                ready, _, _ = select.select([self.master], [], [], 0.05)
                if ready:
                    try:
                        self.buf += os.read(self.master, 4096).decode("utf-8", "replace")
                    except OSError:
                        pass

    def close(self) -> int:
        # Keep draining output while waiting: macOS ptys have small buffers, and a
        # child blocked writing to a full pty never exits.
        deadline = time.monotonic() + 15
        try:
            while self.proc.poll() is None:
                if time.monotonic() > deadline:
                    self.proc.kill()
                    raise AssertionError(f"REPL did not exit; output:\n{self.buf}")
                ready, _, _ = select.select([self.master], [], [], 0.1)
                if ready:
                    try:
                        self.buf += os.read(self.master, 4096).decode("utf-8", "replace")
                    except OSError:
                        pass
            return self.proc.returncode
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
    t.send_eof()  # Ctrl-D exits
    assert t.close() == 0
    assert not (tmp_path / "work/nope").exists()
    assert (tmp_path / "work/y.txt").read_text() == "y"


def test_ctrl_c_cancels_running_tool_but_keeps_session(tmp_path: Path) -> None:
    script = [
        {"tool_calls": [{"name": "run_shell", "input": {"command": "sleep 30; touch late"}}]},
        {"text": "second prompt answered"},
    ]
    t = Term(tmp_path, script, "--mode", "auto")
    t.expect("Type /exit")
    t.send("run the slow thing")
    t.expect("sleep 30")
    time.sleep(0.5)
    os.write(t.master, b"\x03")  # Ctrl-C
    t.expect("interrupted")
    t.send("are you still there?")
    t.expect("second prompt answered")
    t.send("/exit")
    assert t.close() == 0
    time.sleep(0.5)
    assert not (tmp_path / "work/late").exists(), "the cancelled command kept running"


def test_multiline_paste_is_one_prompt(tmp_path: Path) -> None:
    # A second prompt would exhaust the one-turn script and print an error.
    t = Term(tmp_path, [{"text": "got both lines"}])
    t.expect("Type /exit")
    os.write(t.master, b"first line\nsecond line\n")
    t.expect("got both lines")
    t.send("/exit")
    assert t.close() == 0
    assert "exhausted" not in t.buf
    (saved,) = (tmp_path / "state/cli-agent/sessions").glob("*.json")
    first = json.loads(saved.read_text())["messages"][0]
    assert first["content"][0]["text"] == "first line\nsecond line"


def test_command_typed_during_a_run_still_applies(tmp_path: Path) -> None:
    script = [
        {"tool_calls": [{"name": "run_shell", "input": {"command": "sleep 2"}}]},
        {"text": "slept"},
        {"tool_calls": [{"name": "write_file", "input": {"path": "g.txt", "content": "g"}}]},
        {"text": "after"},
    ]
    t = Term(tmp_path, script, "--mode", "auto")
    t.expect("Type /exit")
    t.send("sleep please")
    t.expect("sleep 2")
    # Typed ahead while the command runs; both lines are waiting when the prompt returns.
    t.send("/mode read-only")
    t.send("write g")
    t.expect("after")
    t.send("/exit")
    assert t.close() == 0
    assert "mode is now read-only" in t.buf
    assert not (tmp_path / "work/g.txt").exists()


def test_ctrl_d_right_after_a_prompt_runs_it_then_exits(tmp_path: Path) -> None:
    t = Term(tmp_path, [{"text": "answered"}])
    t.expect("Type /exit")
    t.expect("›")
    time.sleep(0.3)  # let readline take the tty (see send_eof)
    os.write(t.master, b"hello\n\x04")
    t.expect("answered")
    assert t.close() == 0
