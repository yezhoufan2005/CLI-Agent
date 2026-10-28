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
import contextlib
import fcntl
import hashlib
import json
import os
import secrets
from collections.abc import Callable, Iterator
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
    """The file is the source of truth. Every change runs under an exclusive
    lock as load, modify, save, so several cli-agent processes in one
    workspace don't lose each other's checkpoints."""

    def __init__(self, path: Path, root: Path):
        self.path = path
        self.root = root.resolve()
        self._current: str | None = None
        """Id of the checkpoint this process is recording into."""

    def begin(self, label: str) -> None:
        cid = secrets.token_hex(8)
        self._current = cid

        def add(stack: list[dict[str, Any]]) -> None:
            # Prompts that changed nothing must not push real checkpoints out of the
            # cap. Another live process's empty checkpoint may still get files, so
            # only ours and those of exited processes are dropped.
            stack[:] = [cp for cp in stack if cp["files"] or _owner_alive(cp)]
            stack.append(
                {
                    "id": cid,
                    "owner": os.getpid(),
                    "label": " ".join(label.split())[:80],
                    "files": {},
                }
            )
            del stack[:-MAX_CHECKPOINTS]

        self._update(add)

    def record(self, path: Path, before: bytes | None, after: str) -> None:
        """Record a write. `before` is the file's exact bytes before this write
        (None if it did not exist); `after` is the text the agent wrote."""
        if self._current is None:
            self.begin("(unlabelled)")
        rel = str(path.relative_to(self.root))
        written = _digest(_encode(after))

        def add(stack: list[dict[str, Any]]) -> None:
            cp = next((c for c in stack if c.get("id") == self._current), None)
            if cp is None:  # evicted by other processes meanwhile; start again
                cp = {"id": self._current, "label": "(continued)", "files": {}}
                stack.append(cp)
                del stack[:-MAX_CHECKPOINTS]
            entry = cp["files"].get(rel)
            if entry:
                original = entry["original"]
            else:
                original = None if before is None else base64.b64encode(before).decode("ascii")
            cp["files"][rel] = {"original": original, "written": written}

        self._update(add)

    def undo(self) -> UndoReport | None:
        """Revert the most recent checkpoint that changed files; None if none."""
        found: list[dict[str, Any]] = []

        def take(stack: list[dict[str, Any]]) -> None:
            while stack and not stack[-1]["files"]:
                stack.pop()
            if stack:
                found.append(stack.pop())

        with self._locked():
            stack = self._load()
            take(stack)
            self._save(stack)
            if not found:
                return None
            return self._revert(found[0])

    def _revert(self, cp: dict[str, Any]) -> UndoReport:
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
        return report

    def _update(self, change: Callable[[list[dict[str, Any]]], None]) -> None:
        with self._locked():
            stack = self._load()
            change(stack)
            self._save(stack)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = self.path.with_suffix(".lock")
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # closing releases the lock

    def _load(self) -> list[dict[str, Any]]:
        # A corrupt or older-format file is discarded rather than trusted.
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
            return []
        stack = data.get("checkpoints")
        if not isinstance(stack, list):
            return []
        return stack if _valid(stack) else []

    def _save(self, stack: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)
            json.dump({"version": FORMAT_VERSION, "checkpoints": stack}, fh)
        os.replace(tmp, self.path)


def _owner_alive(cp: dict[str, Any]) -> bool:
    """True if the checkpoint belongs to another process that is still running."""
    owner = cp.get("owner")
    if not isinstance(owner, int) or owner == os.getpid():
        return False
    try:
        os.kill(owner, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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
