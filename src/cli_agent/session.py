"""Session persistence: one JSON document per session, rewritten atomically
after every turn so a killed process loses at most the in-flight turn."""

from __future__ import annotations

import atexit
import contextlib
import fcntl
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cli_agent import log
from cli_agent.messages import (
    Message,
    TextBlock,
    ToolResultBlock,
    Usage,
    message_from_dict,
    message_to_dict,
)

SCHEMA_VERSION = 1
INTERRUPTED = "[interrupted: the tool call did not complete]"


def default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "cli-agent"


def new_session_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)


@dataclass
class Session:
    id: str
    cwd: str
    model: str
    created: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated: str = ""
    messages: list[Message] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "id": self.id,
            "cwd": self.cwd,
            "model": self.model,
            "created": self.created,
            "updated": self.updated,
            "title": self.title,
            "usage": self.usage.to_dict(),
            "messages": [message_to_dict(m) for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        version = data.get("version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported session schema version {version!r}")
        usage = Usage(**{k: int(v) for k, v in (data.get("usage") or {}).items()})
        return cls(
            id=str(data["id"]),
            cwd=str(data["cwd"]),
            model=str(data.get("model", "")),
            created=str(data.get("created", "")),
            updated=str(data.get("updated", "")),
            title=str(data.get("title", "")),
            usage=usage,
            messages=[message_from_dict(m) for m in data["messages"]],
        )

    def repair(self) -> bool:
        """Make the history valid for the API after an interrupted run.

        Every tool_use in the final assistant message needs a matching
        tool_result. Returns True if anything was changed.
        """
        if not self.messages or self.messages[-1].role != "assistant":
            return False
        pending = self.messages[-1].tool_uses()
        if not pending:
            return False
        self.messages.append(
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id=t.id, content=INTERRUPTED, is_error=True)
                    for t in pending
                ],
            )
        )
        return True

    def add_user_text(self, text: str) -> None:
        """Append user text, merging into a trailing user message to keep roles alternating."""
        if self.messages and self.messages[-1].role == "user":
            self.messages[-1].content.append(TextBlock(text=text))
        else:
            self.messages.append(Message(role="user", content=[TextBlock(text=text)]))
        if not self.title:
            self.title = " ".join(text.split())[:80]


class SessionBusy(ValueError):
    """Another process has the session open; both saving would lose one's turns."""


class SessionStore:
    def __init__(self, directory: Path | None = None):
        self.dir = (directory or default_state_dir()) / "sessions"
        self._locks: dict[str, int] = {}

    def claim(self, session_id: str) -> str:
        """Hold the session's lock until `release` or process exit.

        Every save rewrites the whole history, so two processes on one session
        would silently drop each other's turns. The lock is a separate file
        because `save` replaces the session file (and with it any lock on it).
        Returns a warning if the lock could not be taken; work goes on without it.
        """
        if session_id in self._locks:
            return ""
        lock = self.path(session_id).with_suffix(".lock")
        for _ in range(5):
            try:
                self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                # flock needs no write access, so a read-only lock file still works.
                fd = os.open(lock, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            except OSError as err:
                return self._no_lock(session_id, err)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held, current = os.fstat(fd), os.stat(lock, follow_symlinks=False)
            except BlockingIOError:
                os.close(fd)
                raise SessionBusy(
                    f"session {session_id} is open in another cli-agent process; "
                    "finish it there, or start a new session"
                ) from None
            except OSError as err:  # no flock support here, or the file was just removed
                os.close(fd)
                if isinstance(err, FileNotFoundError):
                    continue
                return self._no_lock(session_id, err)
            if (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino):
                if not self._locks:
                    atexit.register(self.release_all)
                self._locks[session_id] = fd
                return ""
            os.close(fd)  # locked a file that was replaced meanwhile; take the new one
        return self._no_lock(session_id, OSError("the lock file keeps changing"))

    def _no_lock(self, session_id: str, err: OSError) -> str:
        log.event("session_lock_error", error=str(err)[:300])
        return (
            f"could not lock session {session_id} ({err}); "
            "another cli-agent process could open it at the same time"
        )

    def release(self, session_id: str) -> None:
        fd = self._locks.pop(session_id, None)
        if fd is None:
            return
        # Unlink while still holding the lock: a process that opened the old file
        # meanwhile sees a different inode after locking it and starts over.
        with contextlib.suppress(OSError):
            os.unlink(self.path(session_id).with_suffix(".lock"))
        os.close(fd)

    def release_all(self) -> None:
        for session_id in list(self._locks):
            self.release(session_id)

    def path(self, session_id: str) -> Path:
        if (
            not session_id
            or "/" in session_id
            or session_id.startswith(".")
            or len(session_id) > 100
        ):
            raise ValueError(f"invalid session id {session_id!r}")
        return self.dir / f"{session_id}.json"

    def save(self, session: Session) -> None:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        session.updated = datetime.now(UTC).isoformat()
        target = self.path(session.id)
        tmp = target.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)  # O_CREAT's mode only applies to new files
            # ASCII escapes keep lone surrogates (possible in model JSON) writable.
            json.dump(session.to_dict(), fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)

    def load(self, session_id: str) -> Session:
        path = self.path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"no session {session_id!r} in {self.dir}")
        try:
            session = Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if session.id != session_id:
                # Saving would overwrite the file of the session named inside.
                raise ValueError(f"it names a different session id {session.id!r}")
            return session
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as err:
            raise ValueError(
                f"session file {path} is corrupt or not a valid session: {err}"
            ) from err

    def resolve_id(self, prefix: str) -> str:
        """Expand a unique prefix (or the exact id) to a full session id."""
        self.path(prefix)  # validates the characters
        if (self.dir / f"{prefix}.json").exists():
            return prefix
        # Compare literally: glob would treat `*`, `?` and `[` in the prefix as patterns.
        matches = (
            sorted(
                p.stem for p in self.dir.glob("*.json") if p.stem.startswith(prefix) and p.is_file()
            )
            if self.dir.exists()
            else []
        )
        if not matches:
            raise FileNotFoundError(f"no session {prefix!r} in {self.dir}")
        if len(matches) > 1:
            shown = ", ".join(matches[:5]) + (" …" if len(matches) > 5 else "")
            raise ValueError(f"session id {prefix!r} is ambiguous: {shown}")
        return matches[0]

    def list(self) -> list[Session]:
        if not self.dir.exists():
            return []
        sessions = []
        for p in self.dir.glob("*.json"):
            try:
                session = Session.from_dict(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
            # A copied file naming another id would overwrite that session on save.
            if session.id == p.stem:
                sessions.append(session)
        return sorted(sessions, key=lambda s: s.updated, reverse=True)

    def latest_for(self, cwd: str) -> Session | None:
        for s in self.list():
            if s.cwd == cwd:
                return s
        return None
