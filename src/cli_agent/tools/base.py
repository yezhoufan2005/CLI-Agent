"""Tool interface, workspace path confinement, and output bounding."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from cli_agent.providers.base import ToolSpec
from cli_agent.sandbox import Sandbox

# What a tool can do to the world. Drives the permission decision.
Effect = Literal["read", "write", "execute"]

MAX_OUTPUT_CHARS = 30_000


class ToolError(Exception):
    """A tool failed in a way the model should see and can recover from."""


@dataclass
class ToolContext:
    root: Path
    """Workspace root. File tools may not touch anything outside it."""
    env: dict[str, str] = field(default_factory=dict)
    shell_timeout: float = 120.0
    read_files: set[Path] = field(default_factory=set)
    """Files the model has read this session; edits require a prior read."""
    sandbox: Sandbox | None = None
    """When set, shell commands run inside it."""


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    display: str = ""
    """Extra detail for the user only (e.g. a diff); never sent to the model."""


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]
    effect: Effect

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...

    def summarize(self, args: dict[str, Any]) -> str:
        """One-line human description used in approval prompts and logs."""
        ...


def spec_of(tool: Tool) -> ToolSpec:
    return ToolSpec(name=tool.name, description=tool.description, input_schema=tool.input_schema)


def resolve_in_root(root: Path, raw: object) -> Path:
    """Resolve a model-supplied path and refuse anything outside `root`.

    Symlinks are resolved before the containment check, so a link inside the
    workspace that points outside it is rejected too.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("path must be a non-empty string")
    if "\x00" in raw:
        raise ToolError("path contains a NUL byte")
    root = root.resolve()
    candidate = Path(os.path.expanduser(raw)) if raw.startswith("~") else Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ToolError(f"path {raw!r} is outside the workspace {root}")
    return resolved


def display_path(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(root.resolve())
    except ValueError:
        return str(path)
    return str(rel) if str(rel) != "." else "."


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Keep head and tail of oversized output so errors at the end stay visible."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n[... {omitted} characters omitted ...]\n\n{text[-tail:]}"


def require_str(args: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ToolError(f"missing or non-string argument {key!r}")
    if not allow_empty and value == "":
        raise ToolError(f"argument {key!r} must not be empty")
    return value


def optional_int(args: dict[str, Any], key: str, default: int | None = None) -> int | None:
    value = args.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"argument {key!r} must be an integer")
    return int(value)
