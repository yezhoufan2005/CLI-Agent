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
        '{"checkpoints": [{"label": "x", "files": {"../victim.txt": '
        '{"original": null, "written": "0"}}}]}'
    )
    report = Checkpoints(cps.path, ctx.root).undo()
    assert report is not None and report.skipped == ["../victim.txt"]
    assert victim.read_text() == "keep"
