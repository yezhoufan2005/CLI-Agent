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
    kinds = [e["event"] for e in events if e["event"] != "notice"]
    assert kinds == ["tool_start", "tool_end", "text", "result"]
    assert any("not a git repository" in e.get("message", "") for e in events)
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


def test_sessions_limit(tmp_path: Path) -> None:
    for i in range(3):
        assert run_cli(tmp_path, [{"text": "ok"}], f"task {i}").returncode == 0
    r = run_cli(tmp_path, [], "sessions")
    assert r.returncode == 0 and len(r.stdout.splitlines()) == 3
    r = run_cli(tmp_path, [], "sessions", "--limit", "2")
    assert r.returncode == 0 and len(r.stdout.splitlines()) == 2
    r = run_cli(tmp_path, [], "sessions", "--limit", "0")
    assert r.returncode == 0 and len(r.stdout.splitlines()) == 3
    r = run_cli(tmp_path, [], "sessions", "--limit", "-1")
    assert r.returncode == 2 and "--limit" in r.stderr


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


def _git(work: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(work), *args],
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


def test_git_warnings(tmp_path: Path) -> None:
    r = run_cli(tmp_path, [{"text": "x"}], "hi")
    assert "not a git repository" in r.stderr
    work = tmp_path / "work"
    _git(work, "init", "-q")
    (work / "f.txt").write_text("a")
    _git(work, "add", "f.txt")
    _git(work, "commit", "-qm", "init")
    r = run_cli(tmp_path, [{"text": "x"}], "hi")
    assert "git" not in r.stderr
    (work / "f.txt").write_text("b")
    r = run_cli(tmp_path, [{"text": "x"}], "hi")
    assert "uncommitted changes" in r.stderr
    r = run_cli(tmp_path, [{"text": "x"}], "--mode", "read-only", "hi")
    assert "uncommitted" not in r.stderr


def test_debug_log_has_events_but_no_content(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "s.txt").write_text("FILE-CONTENT-MARKER\n")
    script = [
        {
            "text": "MODEL-TEXT-MARKER",
            "tool_calls": [{"name": "read_file", "input": {"path": "s.txt"}}],
        },
        {"text": "done"},
    ]
    logf = tmp_path / "debug.jsonl"
    r = run_cli(
        tmp_path,
        script,
        "--mode",
        "auto",
        "--debug-log",
        str(logf),
        "PROMPT-MARKER",
        extra_env={"MY_API_KEY": "KEY-MARKER"},
    )
    assert r.returncode == 0, r.stderr
    text = logf.read_text()
    events = [json.loads(line)["event"] for line in text.splitlines()]
    assert events.count("model_call") == 2 and "tool" in events and "permission" in events
    for marker in ("FILE-CONTENT", "MODEL-TEXT", "PROMPT-MARKER", "KEY-MARKER"):
        assert marker not in text
    assert logf.stat().st_mode & 0o777 == 0o600
    assert "tokens in 20, out 10" in r.stderr


def test_search_works_through_python_m_entry(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "f.py").write_text("NEEDLE = 1\n")
    script = [
        {"tool_calls": [{"name": "search_files", "input": {"pattern": "NEEDLE"}}]},
        {"text": "found"},
    ]
    r = run_cli(tmp_path, script, "--json", "find it")
    assert r.returncode == 0, r.stderr
    ends = [json.loads(x) for x in r.stdout.splitlines() if '"tool_end"' in x]
    assert "f.py:1: NEEDLE = 1" in ends[0]["output"]


def test_undo_subcommand(tmp_path: Path) -> None:
    script = [
        {"tool_calls": [{"name": "write_file", "input": {"path": "made.txt", "content": "x"}}]},
        {"text": "made"},
    ]
    r = run_cli(tmp_path, script, "--mode", "auto-edit", "make a file")
    assert r.returncode == 0 and (tmp_path / "work/made.txt").exists()
    r = run_cli(tmp_path, [], "undo")
    assert r.returncode == 0 and "deleted made.txt" in r.stdout
    assert not (tmp_path / "work/made.txt").exists()
    r = run_cli(tmp_path, [], "undo")
    assert "nothing to undo" in r.stdout


def test_mcp_tool_used_end_to_end(tmp_path: Path) -> None:
    server = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
    config = (
        'provider = "fake"\n[mcp_servers.echo]\n'
        f"command = [{json.dumps(sys.executable)}, {json.dumps(str(server))}]\n"
    )
    script = [
        {"tool_calls": [{"name": "mcp__echo__echo", "input": {"text": "ping"}}]},
        {"text": "done"},
    ]
    r = run_cli(tmp_path, script, "--mode", "auto", "--json", "use echo", config=config)
    assert r.returncode == 0, r.stderr
    ends = [json.loads(x) for x in r.stdout.splitlines() if '"tool_end"' in x]
    assert ends[0]["output"] == "echo: ping"


def test_session_token_budget_exit_code(tmp_path: Path) -> None:
    script = [{"tool_calls": [{"name": "list_files", "input": {}}]}] * 5
    r = run_cli(tmp_path, script, "--max-session-tokens", "15", "loop")
    assert r.returncode == 3 and "token budget reached" in r.stderr


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux-only")
def test_no_child_process_inherits_credentials(tmp_path: Path) -> None:
    # search_files starts a multiprocessing helper; it must not hold the key either.
    (tmp_path / "work").mkdir()
    cmd = (
        "for p in /proc/[0-9]*; do grep -qa FAKE_KEY_VALUE $p/environ 2>/dev/null "
        "&& echo LEAK; done; true"
    )
    script = [
        {"tool_calls": [{"name": "search_files", "input": {"pattern": "x"}}]},
        {"tool_calls": [{"name": "run_shell", "input": {"command": cmd}}]},
        {"text": "done"},
    ]
    r = run_cli(
        tmp_path,
        script,
        "--mode",
        "auto",
        "--json",
        "go",
        extra_env={"ANTHROPIC_API_KEY": "FAKE_KEY_VALUE"},
    )
    assert r.returncode == 0, r.stderr
    ends = [json.loads(x) for x in r.stdout.splitlines() if '"tool_end"' in x]
    shell_out = ends[-1]["output"]
    assert "LEAK" not in shell_out


def test_doctor_reports_environment(tmp_path: Path) -> None:
    config = 'provider = "fake"\n[mcp_servers.gone]\ncommand = ["/nonexistent/srv"]\n'
    r = run_cli(tmp_path, [{"text": "OK"}], "doctor", config=config)
    assert "✓ bash:" in r.stdout and "state directory writable" in r.stdout
    assert "shell sandbox" in r.stdout
    assert "✗ MCP server gone" in r.stdout
    assert "replied: 'OK'" in r.stdout
