"""File tools: read, write, exact-string edit, list, search."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from cli_agent.tools.base import (
    Effect,
    ToolContext,
    ToolError,
    ToolResult,
    display_path,
    optional_int,
    require_str,
    resolve_in_root,
    truncate,
)

MAX_READ_LINES = 2000
MAX_LINE_CHARS = 2000
MAX_FILE_BYTES = 5_000_000
# Directories never worth walking for list/search.
SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".tox",
    }
)


def _read_text(path: Path) -> str:
    if not path.exists():
        raise ToolError(f"file not found: {path}")
    if path.is_dir():
        raise ToolError(f"{path} is a directory; use list_files")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        raise ToolError(f"{path} looks like a binary file")
    return data.decode("utf-8", errors="replace")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.cli-agent.tmp")
    mode = path.stat().st_mode & 0o7777 if path.exists() else None
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


class ReadFile:
    name = "read_file"
    effect: Effect = "read"
    description = (
        "Read a UTF-8 text file in the workspace. Returns lines prefixed with line numbers "
        "(the prefix is not part of the file). Use offset/limit for large files."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path, relative to the workspace root."},
            "offset": {"type": "integer", "description": "1-based first line to read."},
            "limit": {"type": "integer", "description": f"Max lines (default {MAX_READ_LINES})."},
        },
        "required": ["path"],
    }

    def summarize(self, args: dict[str, Any]) -> str:
        return f"read {args.get('path')}"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = resolve_in_root(ctx.root, args.get("path"))
        offset = optional_int(args, "offset", 1) or 1
        limit = optional_int(args, "limit", MAX_READ_LINES) or MAX_READ_LINES
        if offset < 1 or limit < 1:
            raise ToolError("offset and limit must be positive")
        text = _read_text(path)
        ctx.read_files.add(path)
        lines = text.splitlines()
        if not lines:
            return ToolResult("(empty file)")
        chunk = lines[offset - 1 : offset - 1 + limit]
        if not chunk:
            raise ToolError(f"offset {offset} is past the end of the file ({len(lines)} lines)")
        out = []
        for i, line in enumerate(chunk, start=offset):
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + " [line truncated]"
            out.append(f"{i:6}\t{line}")
        end = offset + len(chunk) - 1
        if end < len(lines):
            out.append(f"[showing lines {offset}-{end} of {len(lines)}; use offset to read more]")
        return ToolResult(truncate("\n".join(out)))


class WriteFile:
    name = "write_file"
    effect: Effect = "write"
    description = (
        "Create or overwrite a file with the given content. Prefer edit_file for changes to "
        "existing files. Overwriting an existing file requires reading it first."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "Full new file content."},
        },
        "required": ["path", "content"],
    }

    def summarize(self, args: dict[str, Any]) -> str:
        content = args.get("content")
        size = len(content) if isinstance(content, str) else 0
        return f"write {args.get('path')} ({size} chars)"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = resolve_in_root(ctx.root, args.get("path"))
        content = require_str(args, "content", allow_empty=True)
        if path.is_dir():
            raise ToolError(f"{path} is a directory")
        existed = path.exists()
        if existed and path not in ctx.read_files:
            raise ToolError("file exists; read it with read_file before overwriting it")
        _atomic_write(path, content)
        ctx.read_files.add(path)
        verb = "overwrote" if existed else "created"
        return ToolResult(f"{verb} {display_path(ctx.root, path)} ({len(content)} chars)")


class EditFile:
    name = "edit_file"
    effect: Effect = "write"
    description = (
        "Replace an exact string in a file. old_string must match the file exactly "
        "(including whitespace) and be unique unless replace_all is true. "
        "The file must have been read with read_file first."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "Exact text to replace."},
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence."},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def summarize(self, args: dict[str, Any]) -> str:
        return f"edit {args.get('path')}"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = resolve_in_root(ctx.root, args.get("path"))
        old = require_str(args, "old_string")
        new = require_str(args, "new_string", allow_empty=True)
        replace_all = bool(args.get("replace_all", False))
        if old == new:
            raise ToolError("old_string and new_string are identical")
        if path not in ctx.read_files:
            raise ToolError("read the file with read_file before editing it")
        text = _read_text(path)
        count = text.count(old)
        if count == 0:
            hint = ""
            if old.strip() and old.strip() in text:
                hint = " (it matches if leading/trailing whitespace is ignored; check indentation)"
            raise ToolError(f"old_string not found in {display_path(ctx.root, path)}{hint}")
        if count > 1 and not replace_all:
            raise ToolError(
                f"old_string occurs {count} times; add surrounding context to make it unique "
                "or set replace_all"
            )
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        _atomic_write(path, updated)
        line = text[: text.index(old)].count("\n") + 1
        n = count if replace_all else 1
        return ToolResult(
            f"edited {display_path(ctx.root, path)}: {n} replacement(s), first at line {line}"
        )


def _walk(root: Path, start: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            found.append(Path(dirpath) / name)
    return found


class ListFiles:
    name = "list_files"
    effect: Effect = "read"
    description = (
        "List files under a directory (recursively), optionally filtered by a glob pattern "
        "such as '*.py' or 'src/**/test_*.py'. Skips .git, node_modules, virtualenvs."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory (default: workspace root)."},
            "pattern": {"type": "string", "description": "Glob matched against relative paths."},
        },
    }
    max_entries = 500

    def summarize(self, args: dict[str, Any]) -> str:
        return f"list {args.get('path') or '.'} {args.get('pattern') or ''}".strip()

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        start = resolve_in_root(ctx.root, args.get("path") or ".")
        if not start.is_dir():
            raise ToolError(f"{args.get('path')} is not a directory")
        pattern = args.get("pattern")
        if pattern is not None and not isinstance(pattern, str):
            raise ToolError("pattern must be a string")
        root = ctx.root.resolve()
        rels = []
        for p in _walk(root, start):
            rel = display_path(root, p)
            if pattern and not (fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern)):
                continue
            rels.append(rel)
        if not rels:
            return ToolResult("(no matching files)")
        shown = rels[: self.max_entries]
        more = len(rels) - len(shown)
        tail = f"\n[{more} more files not shown; narrow the path or pattern]" if more else ""
        return ToolResult("\n".join(shown) + tail)


class SearchFiles:
    name = "search_files"
    effect: Effect = "read"
    description = (
        "Search file contents with a Python regular expression. Returns path:line: text "
        "for matching lines. Optionally restrict to files matching a glob."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression."},
            "path": {"type": "string", "description": "Directory or file (default: root)."},
            "glob": {"type": "string", "description": "Only search files matching this glob."},
            "ignore_case": {"type": "boolean"},
        },
        "required": ["pattern"],
    }
    max_matches = 200

    def summarize(self, args: dict[str, Any]) -> str:
        return f"search {args.get('pattern')!r} in {args.get('path') or '.'}"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = require_str(args, "pattern")
        try:
            regex = re.compile(raw, re.IGNORECASE if args.get("ignore_case") else 0)
        except re.error as err:
            raise ToolError(f"invalid regular expression: {err}") from err
        start = resolve_in_root(ctx.root, args.get("path") or ".")
        glob = args.get("glob")
        root = ctx.root.resolve()
        files = [start] if start.is_file() else _walk(root, start)
        matches: list[str] = []
        total = 0
        for f in files:
            rel = display_path(root, f)
            if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(f.name, glob)):
                continue
            try:
                if f.stat().st_size > MAX_FILE_BYTES:
                    continue
                data = f.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:8192]:
                continue
            for lineno, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if regex.search(line):
                    total += 1
                    if len(matches) < self.max_matches:
                        matches.append(f"{rel}:{lineno}: {line[:300]}")
        if not matches:
            return ToolResult("(no matches)")
        more = total - len(matches)
        tail = f"\n[{more} more matches not shown]" if more else ""
        return ToolResult(truncate("\n".join(matches) + tail))
