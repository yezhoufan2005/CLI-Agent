"""CLI-level tests: run the real entry point as a subprocess with the fake provider."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_cli(
    tmp_path: Path,
    script: list,
    *args: str,
    stdin: str | None = None,
    extra_env: dict[str, str] | None = None,
    config: str = 'provider = "fake"\n',
) -> subprocess.CompletedProcess[str]:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    cfg = tmp_path / "config.toml"
    cfg.write_text(config)
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(script))
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLI_AGENT_CONFIG": str(cfg),
        "CLI_AGENT_FAKE_SCRIPT": str(script_path),
    }
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "cli_agent", "-C", str(work), *args],
        input=stdin if stdin is not None else "",
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def test_one_shot_prompt_edits_file(tmp_path: Path) -> None:
    script = [
        {"tool_calls": [{"name": "write_file", "input": {"path": "hello.txt", "content": "hi"}}]},
        {"text": "Created hello.txt"},
    ]
    r = run_cli(tmp_path, script, "--mode", "auto-edit", "make a file")
    assert r.returncode == 0, r.stderr
    assert "Created hello.txt" in r.stdout
    assert (tmp_path / "work/hello.txt").read_text() == "hi"
    sessions = list((tmp_path / "state/cli-agent/sessions").glob("*.json"))
    assert len(sessions) == 1


def test_prompt_from_stdin_and_json_events(tmp_path: Path) -> None:
    script = [{"tool_calls": [{"name": "list_files", "input": {}}]}, {"text": "listed"}]
    r = run_cli(tmp_path, script, "--json", stdin="list files\n")
    assert r.returncode == 0, r.stderr
    events = [json.loads(line) for line in r.stdout.splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds == ["tool_start", "tool_end", "text", "result"]
    assert events[-1]["status"] == "done" and events[-1]["text"] == "listed"


def test_non_interactive_ask_mode_denies_writes(tmp_path: Path) -> None:
    script = [
        {"tool_calls": [{"name": "run_shell", "input": {"command": "touch pwned"}}]},
        {"text": "could not"},
    ]
    r = run_cli(tmp_path, script, "--mode", "ask", "do it")
    assert r.returncode == 0
    assert not (tmp_path / "work/pwned").exists()
    assert "not approved" in r.stderr


def test_dangerous_command_blocked_even_in_auto(tmp_path: Path) -> None:
    script = [
        {"tool_calls": [{"name": "run_shell", "input": {"command": "rm -rf ~"}}]},
        {"text": "blocked"},
    ]
    r = run_cli(tmp_path, script, "--mode", "auto", "clean")
    assert "built-in guard" in r.stderr


def test_provider_failure_exit_code_and_resume_hint(tmp_path: Path) -> None:
    r = run_cli(tmp_path, [], "hello")  # empty script -> provider error
    assert r.returncode == 1
    assert "resume with: cli-agent --resume" in r.stderr


def test_max_turns_exit_code(tmp_path: Path) -> None:
    script = [{"tool_calls": [{"name": "list_files", "input": {}}]}] * 3
    r = run_cli(tmp_path, script, "--max-turns", "1", "loop")
    assert r.returncode == 3


def test_continue_resumes_latest_session(tmp_path: Path) -> None:
    r1 = run_cli(tmp_path, [{"text": "first"}], "one")
    assert r1.returncode == 0
    r2 = run_cli(tmp_path, [{"text": "second"}], "--continue", "--json", "two")
    assert r2.returncode == 0, r2.stderr
    result = json.loads(r2.stdout.splitlines()[-1])
    path = tmp_path / "state/cli-agent/sessions" / f"{result['session_id']}.json"
    data = json.loads(path.read_text())
    texts = [b.get("text") for m in data["messages"] for b in m["content"]]
    assert texts == ["one", "first", "two", "second"]


def test_continue_without_session_is_usage_error(tmp_path: Path) -> None:
    r = run_cli(tmp_path, [{"text": "x"}], "--continue", "hi")
    assert r.returncode == 2 and "no previous session" in r.stderr


def test_bad_config_is_usage_error(tmp_path: Path) -> None:
    r = run_cli(tmp_path, [], "hi", config='mode = "yolo"\n')
    assert r.returncode == 2 and "invalid mode" in r.stderr


def test_missing_credentials_message(tmp_path: Path) -> None:
    r = run_cli(tmp_path, [], "hi", config="")
    assert r.returncode == 2 and "ANTHROPIC_API_KEY" in r.stderr


@pytest.mark.parametrize("sub", ["sessions"])
def test_subcommands(tmp_path: Path, sub: str) -> None:
    r = run_cli(tmp_path, [], sub)
    assert r.returncode == 0 and "no saved sessions" in r.stdout


def test_version(tmp_path: Path) -> None:
    r = run_cli(tmp_path, [], "--version")
    assert r.returncode == 0 and r.stdout.startswith("cli-agent ")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux-only")
def test_shell_cannot_read_agent_environ(tmp_path: Path) -> None:
    # The agent's own environment holds credentials; a child command must not be
    # able to read them back through /proc/$PPID/environ.
    cmd = "cat /proc/$PPID/environ | tr '\\0' '\\n' | grep -c FAKE_TOKEN_VALUE || true"
    script = [
        {"tool_calls": [{"name": "run_shell", "input": {"command": cmd}}]},
        {"text": "done"},
    ]
    env_extra = {"MY_API_TOKEN": "FAKE_TOKEN_VALUE"}
    r = run_cli(tmp_path, script, "--mode", "auto", "--json", "go", extra_env=env_extra)
    assert r.returncode == 0, r.stderr
    outputs = [json.loads(x) for x in r.stdout.splitlines()]
    shell_out = next(e["output"] for e in outputs if e["event"] == "tool_end")
    assert "FAKE_TOKEN_VALUE" not in shell_out
    # bash's $PPID is the agent; reading its environ must fail or find nothing.
    assert "Permission denied" in shell_out or shell_out.strip().endswith("0")
