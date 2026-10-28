import os
import sys
from pathlib import Path

import pytest

from cli_agent.tools import ToolContext, ToolError
from cli_agent.tools.base import resolve_in_root, truncate
from cli_agent.tools.files import EditFile, ListFiles, ReadFile, SearchFiles, WriteFile
from cli_agent.tools.shell import RunShell, scrubbed_env


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(root=tmp_path)


def test_resolve_rejects_escape(tmp_path: Path) -> None:
    for bad in ("../x", "/etc/passwd", "~/.ssh/id_rsa", "a/../../b"):
        with pytest.raises(ToolError):
            resolve_in_root(tmp_path, bad)
    assert resolve_in_root(tmp_path, "a/b.txt") == tmp_path.resolve() / "a" / "b.txt"
    assert resolve_in_root(tmp_path, str(tmp_path / "c")) == tmp_path.resolve() / "c"


def test_resolve_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(ToolError, match="outside the workspace"):
        resolve_in_root(tmp_path, "link/secret")


def test_resolve_rejects_bad_input(tmp_path: Path) -> None:
    for bad in (None, "", "  ", 5, "a\x00b"):
        with pytest.raises(ToolError):
            resolve_in_root(tmp_path, bad)


def test_read_file_numbers_and_paging(ctx: ToolContext) -> None:
    (ctx.root / "f.txt").write_text("\n".join(f"line{i}" for i in range(1, 11)))
    out = ReadFile().run({"path": "f.txt", "offset": 3, "limit": 2}, ctx).content
    assert "     3\tline3" in out and "     4\tline4" in out
    assert "line5" not in out.replace("lines 3-4 of 10", "")
    assert "showing lines 3-4 of 10" in out


def test_read_file_errors(ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="not found"):
        ReadFile().run({"path": "missing"}, ctx)
    (ctx.root / "bin").write_bytes(b"\x00\x01\x02")
    with pytest.raises(ToolError, match="binary"):
        ReadFile().run({"path": "bin"}, ctx)
    with pytest.raises(ToolError, match="directory"):
        ReadFile().run({"path": "."}, ctx)
    (ctx.root / "f").write_text("a\n")
    with pytest.raises(ToolError, match="past the end"):
        ReadFile().run({"path": "f", "offset": 5}, ctx)
    with pytest.raises(ToolError, match="integer"):
        ReadFile().run({"path": "f", "offset": "2"}, ctx)


def test_write_requires_read_before_overwrite(ctx: ToolContext) -> None:
    w = WriteFile()
    assert "created" in w.run({"path": "new/dir/a.txt", "content": "hi"}, ctx).content
    assert (ctx.root / "new/dir/a.txt").read_text() == "hi"
    (ctx.root / "b.txt").write_text("old")
    with pytest.raises(ToolError, match="read it"):
        w.run({"path": "b.txt", "content": "new"}, ctx)
    ReadFile().run({"path": "b.txt"}, ctx)
    assert "overwrote" in w.run({"path": "b.txt", "content": "new"}, ctx).content
    assert (ctx.root / "b.txt").read_text() == "new"


def test_write_preserves_mode(ctx: ToolContext) -> None:
    p = ctx.root / "run.sh"
    p.write_text("echo a\n")
    os.chmod(p, 0o755)
    ReadFile().run({"path": "run.sh"}, ctx)
    WriteFile().run({"path": "run.sh", "content": "echo b\n"}, ctx)
    assert p.stat().st_mode & 0o777 == 0o755
    assert not list(ctx.root.glob(".*.tmp"))


def test_edit_file(ctx: ToolContext) -> None:
    p = ctx.root / "m.py"
    p.write_text("x = 1\ny = 1\n")
    e = EditFile()
    with pytest.raises(ToolError, match="read the file"):
        e.run({"path": "m.py", "old_string": "x = 1", "new_string": "x = 2"}, ctx)
    ReadFile().run({"path": "m.py"}, ctx)
    out = e.run({"path": "m.py", "old_string": "y = 1", "new_string": "y = 2"}, ctx).content
    assert "line 2" in out
    assert p.read_text() == "x = 1\ny = 2\n"
    with pytest.raises(ToolError, match="2 times"):
        e.run({"path": "m.py", "old_string": " = ", "new_string": "="}, ctx)
    e.run({"path": "m.py", "old_string": " = ", "new_string": "=", "replace_all": True}, ctx)
    assert p.read_text() == "x=1\ny=2\n"
    with pytest.raises(ToolError, match="not found"):
        e.run({"path": "m.py", "old_string": "zzz", "new_string": "q"}, ctx)
    with pytest.raises(ToolError, match="identical"):
        e.run({"path": "m.py", "old_string": "x", "new_string": "x"}, ctx)


def test_edit_whitespace_hint(ctx: ToolContext) -> None:
    (ctx.root / "m.py").write_text("def f():\n    return 1\n")
    ReadFile().run({"path": "m.py"}, ctx)
    with pytest.raises(ToolError, match="indentation"):
        EditFile().run({"path": "m.py", "old_string": "  return 1  ", "new_string": "x"}, ctx)


def test_list_and_search(ctx: ToolContext) -> None:
    (ctx.root / "src").mkdir()
    (ctx.root / "src/a.py").write_text("import os\nTODO = 1\n")
    (ctx.root / "b.txt").write_text("todo later\n")
    (ctx.root / ".git").mkdir()
    (ctx.root / ".git/config").write_text("TODO\n")
    listing = ListFiles().run({}, ctx).content.splitlines()
    assert listing == ["b.txt", "src/a.py"]
    assert ListFiles().run({"pattern": "*.py"}, ctx).content == "src/a.py"
    hits = SearchFiles().run({"pattern": "todo", "ignore_case": True}, ctx).content
    assert "b.txt:1: todo later" in hits and "src/a.py:2: TODO = 1" in hits
    assert ".git" not in hits
    assert SearchFiles().run({"pattern": "TODO", "glob": "*.txt"}, ctx).content == "(no matches)"
    with pytest.raises(ToolError, match="regular expression"):
        SearchFiles().run({"pattern": "("}, ctx)


def test_shell_basic_and_exit_code(ctx: ToolContext) -> None:
    sh = RunShell()
    ok = sh.run({"command": "echo hello; pwd"}, ctx)
    assert not ok.is_error and "exit code: 0" in ok.content and "hello" in ok.content
    assert str(ctx.root) in ok.content
    bad = sh.run({"command": "echo oops >&2; exit 3"}, ctx)
    assert bad.is_error and "exit code: 3" in bad.content and "oops" in bad.content


def test_shell_timeout_kills_process_group(ctx: ToolContext) -> None:
    marker = ctx.root / "marker"
    res = RunShell().run({"command": f"(sleep 3; touch {marker}) & sleep 30", "timeout": 0.5}, ctx)
    assert res.is_error and "timed out" in res.content
    import time

    time.sleep(3.5)
    assert not marker.exists(), "background child survived the timeout kill"


def test_shell_stdin_is_empty(ctx: ToolContext) -> None:
    res = RunShell().run({"command": "cat; echo done", "timeout": 5}, ctx)
    assert "done" in res.content and not res.is_error


def test_shell_env_scrubbed(ctx: ToolContext) -> None:
    ctx.env = {
        "PATH": os.environ["PATH"],
        "ANTHROPIC_API_KEY": "sk-secret",
        "GH_TOKEN": "t",
        "HARMLESS": "ok",
    }
    res = RunShell().run({"command": "env"}, ctx).content
    assert "sk-secret" not in res and "GH_TOKEN" not in res and "HARMLESS=ok" in res
    for name in (
        "DB_PASS",
        "AWS_ACCESS_KEY_ID",
        "NPM_AUTH",
        "DATABASE_URL",
        "SSH_AUTH_SOCK",
        "SENTRY_DSN",
        "ANTHROPIC_AUTH_TOKEN",
        "GITHUB_TOKEN",
    ):
        assert name not in scrubbed_env({name: "x"}), name
    for name in ("PATH", "HOME", "PASSAGE", "AUTHOR", "LANG"):
        assert name in scrubbed_env({name: "x"}), name
    assert scrubbed_env({"MY_PASSWORD": "x", "A": "b"}) == {
        "A": "b",
        "PAGER": "cat",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
    }


def test_shell_output_bounded(ctx: ToolContext) -> None:
    res = RunShell().run({"command": "yes abcdefgh | head -c 2000000"}, ctx)
    assert len(res.content) < 40_000 and "characters omitted" in res.content


def test_shell_bad_timeout(ctx: ToolContext) -> None:
    for t in (0, -1, "5", True):
        with pytest.raises(ToolError):
            RunShell().run({"command": "true", "timeout": t}, ctx)


def test_truncate_keeps_tail() -> None:
    text = "A" * 100 + "END"
    out = truncate(text, 30)
    assert out.endswith("END") and out.startswith("A") and "omitted" in out
    assert truncate("short", 30) == "short"


def test_atomic_write_ignores_planted_temp_symlink(ctx: ToolContext) -> None:
    outside = ctx.root.parent / f"{ctx.root.name}-victim"
    outside.write_text("original")
    # The old implementation used a predictable temp name; plant links at it anyway.
    (ctx.root / ".a.txt.cli-agent.tmp").symlink_to(outside)
    WriteFile().run({"path": "a.txt", "content": "new"}, ctx)
    assert outside.read_text() == "original"
    assert (ctx.root / "a.txt").read_text() == "new"
    assert sorted(p.name for p in ctx.root.iterdir()) == [".a.txt.cli-agent.tmp", "a.txt"]


def test_new_file_mode_respects_umask(ctx: ToolContext) -> None:
    WriteFile().run({"path": "n.txt", "content": "x"}, ctx)
    mode = (ctx.root / "n.txt").stat().st_mode & 0o777
    current = os.umask(0)
    os.umask(current)
    assert mode == 0o666 & ~current


def test_list_and_search_skip_symlinks(ctx: ToolContext) -> None:
    secret = ctx.root.parent / f"{ctx.root.name}-secret.txt"
    secret.write_text("TOPSECRET\n")
    (ctx.root / "link.txt").symlink_to(secret)
    (ctx.root / "real.txt").write_text("hello\n")
    assert SearchFiles().run({"pattern": "TOPSECRET"}, ctx).content == "(no matches)"
    assert ListFiles().run({}, ctx).content == "real.txt"
    with pytest.raises(ToolError, match="outside"):
        ReadFile().run({"path": "link.txt"}, ctx)


def test_read_rejects_special_files(ctx: ToolContext) -> None:
    os.mkfifo(ctx.root / "fifo")
    with pytest.raises(ToolError, match="regular file"):
        ReadFile().run({"path": "fifo"}, ctx)


def test_walk_limit(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import cli_agent.tools.files as files

    monkeypatch.setattr(files, "MAX_WALK_FILES", 3)
    for i in range(5):
        (ctx.root / f"f{i}.txt").write_text("x\n")
    files_found, truncated = files._walk(ctx.root, ctx.root, limit=3)
    assert len(files_found) == 3 and truncated


def test_write_and_edit_produce_diffs(ctx: ToolContext) -> None:
    res = WriteFile().run({"path": "d.py", "content": "a = 1\n"}, ctx)
    assert "+a = 1" in res.display and "b/d.py" in res.display
    res = EditFile().run({"path": "d.py", "old_string": "a = 1", "new_string": "a = 2"}, ctx)
    assert "-a = 1" in res.display and "+a = 2" in res.display
    # The diff is for the user; the model sees the short summary only.
    assert "a = 2" not in res.content


def test_shell_kills_leftover_background_jobs(ctx: ToolContext) -> None:
    marker = ctx.root / "bg-marker"
    res = RunShell().run({"command": f"(sleep 1; touch {marker}) & echo started"}, ctx)
    assert not res.is_error and "started" in res.content
    import time

    time.sleep(1.5)
    assert not marker.exists(), "background job outlived the command"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="subreaper is Linux-only")
def test_shell_kills_setsid_escapees(ctx: ToolContext) -> None:
    marker = ctx.root / "setsid-marker"
    cmd = (
        f"setsid bash -c 'sleep 1; touch {marker}' >/dev/null 2>&1 < /dev/null & sleep 0.3; echo ok"
    )
    res = RunShell().run({"command": cmd, "timeout": 10}, ctx)
    assert "ok" in res.content
    import time

    time.sleep(1.5)
    assert not marker.exists(), "setsid child escaped cleanup"


def test_shell_runaway_output_killed(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import cli_agent.tools.shell as shell

    monkeypatch.setattr(shell, "MAX_SPOOL_BYTES", 1_000_000)
    res = RunShell().run({"command": "yes", "timeout": 30}, ctx)
    assert res.is_error and "output and was killed" in res.content


def test_search_stops_catastrophic_regex(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    import cli_agent.tools.files as files

    monkeypatch.setattr(files, "SEARCH_TIMEOUT", 2.0)
    (ctx.root / "a.txt").write_text("a" * 40 + "!\n")
    started = time.monotonic()
    with pytest.raises(ToolError, match="longer than"):
        SearchFiles().run({"pattern": "(a+)+$"}, ctx)
    assert time.monotonic() - started < 10
