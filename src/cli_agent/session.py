"""Session persistence: one JSON document per session, rewritten atomically
after every turn so a killed process loses at most the in-flight turn."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


class SessionStore:
    def __init__(self, directory: Path | None = None):
        self.dir = (directory or default_state_dir()) / "sessions"

    def path(self, session_id: str) -> Path:
        if not session_id or "/" in session_id or session_id.startswith("."):
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
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            raise ValueError(f"session file {path} is corrupt: {err}") from err
        return Session.from_dict(data)

    def list(self) -> list[Session]:
        if not self.dir.exists():
            return []
        sessions = []
        for p in self.dir.glob("*.json"):
            try:
                sessions.append(Session.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(sessions, key=lambda s: s.updated, reverse=True)

    def latest_for(self, cwd: str) -> Session | None:
        for s in self.list():
            if s.cwd == cwd:
                return s
        return None
