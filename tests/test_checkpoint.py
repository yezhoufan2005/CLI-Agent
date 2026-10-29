import json
from pathlib import Path

from cli_agent.checkpoint import Checkpoints
from cli_agent.tools import ToolContext
from cli_agent.tools.files import EditFile, ReadFile, WriteFile


def setup(tmp_path: Path) -> tuple[ToolContext, Checkpoints]:
    root = tmp_path / "ws"
    root.mkdir()
    cps = Checkpoints(tmp_path / "state" / "cp.json", root)
    return ToolContext(root=root, checkpoints=cps), cps


def test_undo_restores_edits_and_deletes_created_files(tmp_path: Path) -> None:
    ctx, cps = setup(tmp_path)
    (ctx.root / "a.txt").write_text("one\n")
    cps.begin("change things")
    ReadFile().run({"path": "a.txt"}, ctx)
    EditFile().run({"path": "a.txt", "old_string": "one", "new_string": "two"}, ctx)
    EditFile().run({"path": "a.txt", "old_string": "two", "new_string": "three"}, ctx)
    WriteFile().run({"path": "new/b.txt", "content": "b"}, ctx)
    # A fresh instance (next CLI process) sees the same checkpoints.
    report = Checkpoints(cps.path, ctx.root).undo()
    assert report is not None and report.label == "change things"
    assert (ctx.root / "a.txt").read_text() == "one\n"  # back to before the first edit
    assert not (ctx.root / "new/b.txt").exists()
    assert report.restored == ["a.txt"] and report.deleted == ["new/b.txt"]
    assert Checkpoints(cps.path, ctx.root).undo() is None


def test_undo_skips_files_the_user_changed_later(tmp_path: Path) -> None:
    ctx, cps = setup(tmp_path)
    cps.begin("p")
    WriteFile().run({"path": "c.txt", "content": "agent"}, ctx)
    (ctx.root / "c.txt").write_text("user edit")
    report = cps.undo()
    assert report is not None and report.skipped == ["c.txt"]
    assert (ctx.root / "c.txt").read_text() == "user edit"


def test_undo_goes_back_one_prompt_at_a_time(tmp_path: Path) -> None:
    ctx, cps = setup(tmp_path)
    cps.begin("first")
    WriteFile().run({"path": "f", "content": "1"}, ctx)
    cps.begin("second")
    cps.begin("third (no file changes)")
    ReadFile().run({"path": "f"}, ctx)
    WriteFile().run({"path": "f", "content": "2"}, ctx)
    r = cps.undo()
    assert r is not None and r.label == "third (no file changes)"
    assert (ctx.root / "f").read_text() == "1"
    r = cps.undo()
    assert r is not None and r.label == "first" and not (ctx.root / "f").exists()


def test_tampered_checkpoint_cannot_escape_workspace(tmp_path: Path) -> None:
    ctx, cps = setup(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    cps.path.parent.mkdir(parents=True)
    cps.path.write_text(
        '{"version": 2, "checkpoints": [{"label": "x", "files": {"../victim.txt": '
        '{"original": null, "written": "0"}}}]}'
    )
    report = Checkpoints(cps.path, ctx.root).undo()
    assert report is not None and report.skipped == ["../victim.txt"]
    assert victim.read_text() == "keep"


def test_undo_restores_exact_bytes_of_non_utf8_file(tmp_path: Path) -> None:
    ctx, cps = setup(tmp_path)
    original = "caf\xe9 = 1\r\nx = 'M\xfcller'\r\n".encode("latin-1")
    (ctx.root / "legacy.py").write_bytes(original)
    cps.begin("p")
    ReadFile().run({"path": "legacy.py"}, ctx)
    EditFile().run({"path": "legacy.py", "old_string": "1", "new_string": "2"}, ctx)
    report = cps.undo()
    assert report is not None and report.restored == ["legacy.py"]
    assert (ctx.root / "legacy.py").read_bytes() == original


def test_undo_never_follows_symlink_swapped_in_later(tmp_path: Path) -> None:
    ctx, cps = setup(tmp_path)
    keep = ctx.root / "keep.txt"
    keep.write_text("same")
    cps.begin("p")
    WriteFile().run({"path": "gen.txt", "content": "same"}, ctx)
    (ctx.root / "gen.txt").unlink()
    (ctx.root / "gen.txt").symlink_to(keep)
    report = cps.undo()
    assert report is not None and report.skipped == ["gen.txt"]
    assert keep.read_text() == "same"


def test_empty_prompts_do_not_evict_real_checkpoints(tmp_path: Path) -> None:
    ctx, cps = setup(tmp_path)
    cps.begin("real")
    WriteFile().run({"path": "a", "content": "x"}, ctx)
    for i in range(30):
        cps.begin(f"question {i}")
    report = cps.undo()
    assert report is not None and report.label == "real"


def test_corrupt_checkpoint_file_is_ignored(tmp_path: Path) -> None:
    ctx, cps = setup(tmp_path)
    cps.path.parent.mkdir(parents=True)
    for bad in (
        '{"version": 2, "checkpoints": 5}',
        '{"version": 2, "checkpoints": [{"label": "x"}]}',
        "[1]",
        "{not json",
    ):
        cps.path.write_text(bad)
        assert Checkpoints(cps.path, ctx.root).undo() is None


def test_two_processes_keep_each_others_checkpoints(tmp_path: Path) -> None:
    ctx, cps = setup(tmp_path)
    import cli_agent.checkpoint as cp_mod

    other = Checkpoints(cps.path, ctx.root)  # a second cli-agent process
    ctx_b = ToolContext(root=ctx.root, checkpoints=other)
    cps.begin("A: write x")
    # Pretend A's checkpoint belongs to another live process (pid 1 always exists).
    data = json.loads(cps.path.read_text())
    data["checkpoints"][-1]["owner"] = 1
    cps.path.write_text(json.dumps(data))
    assert cp_mod._owner_alive(data["checkpoints"][-1])
    other.begin("B: write y")
    WriteFile().run({"path": "x", "content": "1"}, ctx)
    WriteFile().run({"path": "y", "content": "2"}, ctx_b)
    labels = []
    while (r := Checkpoints(cps.path, ctx.root).undo()) is not None:
        labels.append(r.label)
    assert sorted(labels) == ["A: write x", "B: write y"]
    assert not (ctx.root / "x").exists() and not (ctx.root / "y").exists()


def test_undo_reports_shell_commands_it_cannot_revert(tmp_path: Path) -> None:
    from cli_agent.tools.shell import RunShell

    ctx, cps = setup(tmp_path)
    cps.begin("mixed")
    WriteFile().run({"path": "a.txt", "content": "a"}, ctx)
    RunShell().run({"command": "echo b > b.txt"}, ctx)
    RunShell().run({"command": "true"}, ctx)
    cps.finish()  # the agent loop calls this when the prompt's run ends
    report = cps.undo()
    assert report is not None and report.deleted == ["a.txt"]
    assert report.shell_commands == 2
    assert (ctx.root / "b.txt").exists()  # documented: shell changes stay


def test_shell_only_prompt_is_reported_not_skipped(tmp_path: Path) -> None:
    from cli_agent.tools.shell import RunShell

    ctx, cps = setup(tmp_path)
    cps.begin("write b")
    WriteFile().run({"path": "b.txt", "content": "b"}, ctx)
    cps.begin("sed it")
    RunShell().run({"command": "echo c > c.txt"}, ctx)
    RunShell().run({"command": "true"}, ctx)
    cps.finish()
    cps.begin("next prompt")  # an empty prompt must not drop the shell-only one
    report = Checkpoints(cps.path, ctx.root).undo()
    assert report is not None and report.label == "sed it"
    assert report.shell_commands == 2 and not report.files_changed
    assert (ctx.root / "b.txt").exists()  # the older prompt is not reverted yet
    report = Checkpoints(cps.path, ctx.root).undo()
    assert report is not None and report.deleted == ["b.txt"]


def test_tampered_shell_count_is_ignored(tmp_path: Path) -> None:
    ctx, cps = setup(tmp_path)
    cps.begin("p")
    WriteFile().run({"path": "a.txt", "content": "a"}, ctx)
    for bad in [True, -3, 2**70, "x", None, [1], 1.5]:
        data = json.loads(cps.path.read_text())
        data["checkpoints"][-1]["shell"] = bad
        cps.path.write_text(json.dumps(data))
        cps.note_shell()  # must not raise
        assert Checkpoints(cps.path, ctx.root)._load()[-1].get("shell") in (bad, 1)


def test_shell_command_runs_even_if_checkpoint_bookkeeping_fails(tmp_path: Path) -> None:
    from cli_agent.tools.shell import RunShell

    ctx, cps = setup(tmp_path)
    cps.begin("p")

    def broken() -> None:
        raise PermissionError("state dir is read-only")

    cps.note_shell = broken  # type: ignore[method-assign]
    RunShell().run({"command": "echo ran > r.txt"}, ctx)
    assert (ctx.root / "r.txt").read_text() == "ran\n"


def test_shell_count_written_once_until_finish(tmp_path: Path) -> None:
    from cli_agent.tools.shell import RunShell

    ctx, cps = setup(tmp_path)
    cps.begin("p")
    for _ in range(3):
        RunShell().run({"command": "true"}, ctx)
    assert cps._load()[-1]["shell"] == 1
    cps.finish()
    assert cps._load()[-1]["shell"] == 3


def test_held_lock_fails_fast_and_the_write_still_happens(tmp_path: Path) -> None:
    import fcntl
    import os
    import time

    import cli_agent.checkpoint as checkpoint

    ctx, cps = setup(tmp_path)
    cps.begin("p")
    fd = os.open(cps.path.with_suffix(".lock"), os.O_WRONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)  # another process holding the lock
    old_timeout = checkpoint.LOCK_TIMEOUT
    checkpoint.LOCK_TIMEOUT = 0.3
    try:
        start = time.monotonic()
        result = WriteFile().run({"path": "a.txt", "content": "a"}, ctx)
        assert time.monotonic() - start < 3
        assert (ctx.root / "a.txt").read_text() == "a"
        assert not result.is_error and "undo will not cover it" in result.content
        assert "locked by another process" in result.content
    finally:
        checkpoint.LOCK_TIMEOUT = old_timeout
        os.close(fd)
    WriteFile().run({"path": "b.txt", "content": "b"}, ctx)  # lock released: recorded again
    report = cps.undo()
    assert report is not None and report.deleted == ["b.txt"]


def test_shell_only_prompts_are_evicted_before_file_changes(tmp_path: Path) -> None:
    from cli_agent.checkpoint import MAX_CHECKPOINTS

    ctx, cps = setup(tmp_path)
    cps.begin("edit app")
    WriteFile().run({"path": "app.py", "content": "x"}, ctx)
    for i in range(MAX_CHECKPOINTS + 5):
        cps.begin(f"shell {i}")
        cps.note_shell()
    cps.begin("last")
    labels = [cp["label"] for cp in cps._load()]
    assert len(labels) == MAX_CHECKPOINTS and labels[0] == "edit app"


def test_newest_shell_only_prompt_survives_the_cap(tmp_path: Path) -> None:
    from cli_agent.checkpoint import MAX_CHECKPOINTS

    ctx, cps = setup(tmp_path)
    for i in range(MAX_CHECKPOINTS - 1):
        cps.begin(f"edit {i}")
        WriteFile().run({"path": f"f{i}.txt", "content": "x"}, ctx)
    cps.begin("shell prompt")
    cps.note_shell()
    cps.begin("question")
    report = Checkpoints(cps.path, ctx.root).undo()
    assert report is not None and report.label == "shell prompt"


def test_failed_undo_keeps_what_it_could_not_revert(tmp_path: Path) -> None:
    import os

    import pytest

    ctx, cps = setup(tmp_path)
    (ctx.root / "sub").mkdir()
    (ctx.root / "a.txt").write_text("A0")
    (ctx.root / "sub/b.txt").write_text("B0")
    cps.begin("p")
    for rel, text in (("a.txt", "A1"), ("sub/b.txt", "B1")):
        ReadFile().run({"path": rel}, ctx)
        WriteFile().run({"path": rel, "content": text}, ctx)
    os.chmod(ctx.root / "sub", 0o500)
    try:
        if os.access(ctx.root / "sub", os.W_OK):
            pytest.skip("running as root: directory permissions are not enforced")
        with pytest.raises(OSError, match=r"reverted before the failure: a\.txt"):
            cps.undo()
    finally:
        os.chmod(ctx.root / "sub", 0o700)
    assert (ctx.root / "a.txt").read_text() == "A0"
    report = cps.undo()  # the retry finishes the same prompt
    assert report is not None and report.label == "p" and report.restored == ["sub/b.txt"]
    assert (ctx.root / "sub/b.txt").read_text() == "B0"
