"""File checkpoints so the user can undo what the agent's file tools changed.

Each user prompt starts a checkpoint. The first time a file tool writes a path
during that prompt, the original content (or "did not exist") is recorded,
along with a hash of what the agent last wrote. ``undo`` restores the
originals of the most recent checkpoint, but skips any file that changed since
the agent wrote it, so it never discards the user's own later edits.

Only write_file/edit_file are tracked; changes made by shell commands are not.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_CHECKPOINTS = 20
FORMAT_VERSION = 2


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _valid(stack: object) -> bool:
    if not isinstance(stack, list):
        return False
    for cp in stack:
        if not (isinstance(cp, dict) and isinstance(cp.get("label"), str)):
            return False
        files = cp.get("files")
        if not isinstance(files, dict):
            return False
        for entry in files.values():
            if not (
                isinstance(entry, dict)
                and isinstance(entry.get("written"), str)
                and (entry.get("original") is None or isinstance(entry.get("original"), str))
            ):
                return False
    return True


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
            # A corrupt or older-format file is discarded rather than trusted.
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                stack = data.get("checkpoints") if isinstance(data, dict) else None
                if data.get("version") == FORMAT_VERSION and isinstance(stack, list):
                    self._stack = stack if _valid(stack) else []
            except (OSError, ValueError, AttributeError):
                self._stack = []

    def begin(self, label: str) -> None:
        # Prompts that changed nothing must not push real checkpoints out of the cap.
        self._stack = [cp for cp in self._stack if cp["files"]]
        self._stack.append({"label": " ".join(label.split())[:80], "files": {}})
        del self._stack[:-MAX_CHECKPOINTS]

    def record(self, path: Path, before: bytes | None, after: str) -> None:
        """Record a write. `before` is the file's exact bytes before this write
        (None if it did not exist); `after` is the text the agent wrote."""
        if not self._stack:
            self.begin("(unlabelled)")
        rel = str(path.relative_to(self.root))
        files: dict[str, Any] = self._stack[-1]["files"]
        entry = files.get(rel)
        if entry:
            original = entry["original"]
        else:
            original = None if before is None else base64.b64encode(before).decode("ascii")
        files[rel] = {"original": original, "written": _digest(_encode(after))}
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
        for rel, entry in sorted(cp["files"].items()):
            target = self.root / rel
            # Never follow a symlink (the user may have swapped one in since) and
            # never leave the workspace (a tampered checkpoint file).
            if _has_symlink(self.root, target) or not _inside(self.root, target):
                report.skipped.append(rel)
                continue
            current = _read(target)
            if current is None or _digest(current) != entry["written"]:
                report.skipped.append(rel)
                continue
            if entry["original"] is None:
                target.unlink()
                report.deleted.append(rel)
            else:
                _write(target, base64.b64decode(entry["original"]))
                report.restored.append(rel)
        self._save()
        return report

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)
            json.dump({"version": FORMAT_VERSION, "checkpoints": self._stack}, fh)
        os.replace(tmp, self.path)


def _inside(root: Path, target: Path) -> bool:
    resolved = target.resolve()
    return resolved != root and root in resolved.parents


def _has_symlink(root: Path, target: Path) -> bool:
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return True
    p = root
    for part in parts:
        p = p / part
        if p.is_symlink():
            return True
    return False


def _read(path: Path) -> bytes | None:
    try:
        if not path.is_file():
            return None
        return path.read_bytes()
    except OSError:
        return None


def _encode(text: str) -> bytes:
    from cli_agent.tools.files import encode_text

    return encode_text(text)


def _write(path: Path, data: bytes) -> None:
    from cli_agent.tools.files import _atomic_write_bytes

    _atomic_write_bytes(path, data)
