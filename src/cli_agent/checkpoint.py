"""File checkpoints so the user can undo what the agent's file tools changed.

Each user prompt starts a checkpoint. The first time a file tool writes a path
during that prompt, the original content (or "did not exist") is recorded,
along with a hash of what the agent last wrote. ``undo`` restores the
originals of the most recent checkpoint, but skips any file that changed since
the agent wrote it, so it never discards the user's own later edits.

Only write_file/edit_file are tracked; changes made by shell commands are not.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_CHECKPOINTS = 20


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass
class _Entry:
    original: str | None
    """Content before the agent touched the file; None if it did not exist."""
    written: str
    """Hash of the content the agent last wrote."""


@dataclass
class UndoReport:
    restored: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    """Files changed after the agent wrote them; left alone."""
    label: str = ""


class Checkpoints:
    def __init__(self, path: Path, root: Path):
        self.path = path
        self.root = root.resolve()
        self._stack: list[dict[str, Any]] = []
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._stack = list(data.get("checkpoints", []))
            except (OSError, ValueError, AttributeError):
                self._stack = []

    def begin(self, label: str) -> None:
        self._stack.append({"label": " ".join(label.split())[:80], "files": {}})
        del self._stack[:-MAX_CHECKPOINTS]

    def record(self, path: Path, before: str | None, after: str) -> None:
        if not self._stack:
            self.begin("(unlabelled)")
        rel = str(path.resolve().relative_to(self.root))
        files: dict[str, Any] = self._stack[-1]["files"]
        entry = files.get(rel)
        original = entry["original"] if entry else before
        files[rel] = {"original": original, "written": _digest(after)}
        self._save()

    def has_changes(self) -> bool:
        return any(c["files"] for c in self._stack)

    def undo(self) -> UndoReport | None:
        """Revert the most recent checkpoint that changed files; None if none."""
        while self._stack and not self._stack[-1]["files"]:
            self._stack.pop()
        if not self._stack:
            self._save()
            return None
        cp = self._stack.pop()
        report = UndoReport(label=cp["label"])
        for rel, raw in sorted(cp["files"].items()):
            entry = _Entry(original=raw["original"], written=raw["written"])
            target = (self.root / rel).resolve()
            if target != self.root and self.root not in target.parents:
                report.skipped.append(rel)  # tampered checkpoint file; never escape
                continue
            current = _read(target)
            if current is None or _digest(current) != entry.written:
                report.skipped.append(rel)
                continue
            if entry.original is None:
                target.unlink()
                report.deleted.append(rel)
            else:
                _write(target, entry.original)
                report.restored.append(rel)
        self._save()
        return report

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)
            json.dump({"version": 1, "checkpoints": self._stack}, fh)
        os.replace(tmp, self.path)


def _read(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_bytes().decode("utf-8", errors="surrogateescape")
    except OSError:
        return None


def _write(path: Path, text: str) -> None:
    from cli_agent.tools.files import _atomic_write

    _atomic_write(path, text)
