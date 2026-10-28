"""File tools: read, write, exact-string edit, list, search."""

from __future__ import annotations

import contextlib
import difflib
import fnmatch
import multiprocessing
import os
import re
import stat
import tempfile
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
MAX_WALK_FILES = 20_000
SEARCH_TIMEOUT = 30.0
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
    if not path.is_file():
        raise ToolError(f"{path} is not a regular file")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        raise ToolError(f"{path} looks like a binary file")
    return data.decode("utf-8", errors="replace")


def encode_text(text: str) -> bytes:
    """The bytes written for model-supplied text (lone surrogates become '?')."""
    return text.encode("utf-8", "replace")


def _atomic_write(path: Path, text: str) -> None:
    _atomic_write_bytes(path, encode_text(text))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o666 & ~_umask()
    # mkstemp creates a fresh file with O_EXCL, so a pre-planted symlink at the
    # temp name cannot redirect the write outside the workspace.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def unified_diff(rel: str, before: str, after: str, max_lines: int = 200) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            n=2,
        )
    )
    text = "".join(line if line.endswith("\n") else line + "\n" for line in lines[:max_lines])
    if len(lines) > max_lines:
        text += f"[... {len(lines) - max_lines} more diff lines]\n"
    return text


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


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
        before = _read_text(path) if existed else ""
        before_bytes = path.read_bytes() if existed else None
        _atomic_write(path, content)
        if ctx.checkpoints is not None:
            ctx.checkpoints.record(path, before_bytes, content)
        ctx.read_files.add(path)
        verb = "overwrote" if existed else "created"
        rel = display_path(ctx.root, path)
        return ToolResult(
            f"{verb} {rel} ({len(content)} chars)", display=unified_diff(rel, before, content)
        )


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
            fuzzy = fuzzy_replace(text, old, new)
            if fuzzy is None:
                hint = ""
                if old.strip() and old.strip() in text:
                    hint = " (it matches if surrounding whitespace is ignored; check indentation)"
                raise ToolError(f"old_string not found in {display_path(ctx.root, path)}{hint}")
            # Exactly one place matches once whitespace differences are ignored.
            updated, line = fuzzy
            before_bytes = path.read_bytes()
            _atomic_write(path, updated)
            if ctx.checkpoints is not None:
                ctx.checkpoints.record(path, before_bytes, updated)
            rel = display_path(ctx.root, path)
            return ToolResult(
                f"edited {rel}: 1 replacement at line {line} (old_string matched only after "
                "ignoring whitespace differences; new_string was re-indented to match)",
                display=unified_diff(rel, text, updated),
            )
        if count > 1 and not replace_all:
            raise ToolError(
                f"old_string occurs {count} times; add surrounding context to make it unique "
                "or set replace_all"
            )
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        before_bytes = path.read_bytes()
        _atomic_write(path, updated)
        if ctx.checkpoints is not None:
            ctx.checkpoints.record(path, before_bytes, updated)
        line = text[: text.index(old)].count("\n") + 1
        n = count if replace_all else 1
        rel = display_path(ctx.root, path)
        return ToolResult(
            f"edited {rel}: {n} replacement(s), first at line {line}",
            display=unified_diff(rel, text, updated),
        )


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def fuzzy_replace(text: str, old: str, new: str) -> tuple[str, int] | None:
    """Replace `old` when it matches exactly one run of whole lines except for
    whitespace: trailing spaces, CRLF vs LF, or a uniform extra indentation.

    Returns (updated text, 1-based line) or None if there is no unique match.
    Ambiguity is never resolved by guessing.
    """
    old_lines = old.split("\n")
    if old_lines and old_lines[-1] == "":
        old_lines = old_lines[:-1]
    old_lines = [line.rstrip("\r") for line in old_lines]
    if not any(line.strip() for line in old_lines):
        return None
    file_lines = text.splitlines(keepends=True)
    n = len(old_lines)
    matches: list[tuple[int, str]] = []
    for i in range(len(file_lines) - n + 1):
        window = [w.rstrip("\r\n") for w in file_lines[i : i + n]]
        prefix = _uniform_prefix(window, old_lines)
        if prefix is not None:
            matches.append((i, prefix))
            if len(matches) > 1:
                return None
    if len(matches) != 1:
        return None
    i, prefix = matches[0]
    window = file_lines[i : i + n]
    # Whole lines are replaced, so keep the window's own line endings.
    eol = "\r\n" if window[0].endswith("\r\n") else "\n"
    last_eol = window[-1][len(window[-1].rstrip("\r\n")) :]
    if new == "":
        replacement = ""
    else:
        new_lines = new.removesuffix("\n").split("\n")
        body = eol.join(
            prefix + ln.rstrip("\r") if ln.strip() else ln.rstrip("\r") for ln in new_lines
        )
        replacement = body + last_eol
    updated = "".join(file_lines[:i]) + replacement + "".join(file_lines[i + n :])
    return updated, i + 1


def _uniform_prefix(window: list[str], old_lines: list[str]) -> str | None:
    """The indentation the file adds to every non-blank old line, if the lines
    are otherwise equal (ignoring trailing whitespace); else None."""
    prefix: str | None = None
    for w, o in zip(window, old_lines, strict=True):
        w, o = w.rstrip(), o.rstrip()
        if not o.strip():
            if w.strip():
                return None
            continue
        if w.strip() != o.strip():
            return None
        wi, oi = _indent(w), _indent(o)
        if not wi.endswith(oi):
            return None
        p = wi[: len(wi) - len(oi)]
        if prefix is None:
            prefix = p
        elif p != prefix:
            return None
    return prefix


def _walk(root: Path, start: Path, limit: int = MAX_WALK_FILES) -> tuple[list[Path], bool]:
    """Regular files under `start`, skipping symlinks (they may point outside the
    workspace) and SKIP_DIRS. Returns (files, truncated)."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if len(found) >= limit:
                return found, True
            found.append(p)
    return found, False


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
        files, truncated = _walk(root, start)
        for p in files:
            rel = display_path(root, p)
            if pattern and not (fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern)):
                continue
            rels.append(rel)
        if not rels:
            return ToolResult("(no matching files)")
        shown = rels[: self.max_entries]
        more = len(rels) - len(shown)
        tail = f"\n[{more} more files not shown; narrow the path or pattern]" if more else ""
        if truncated:
            tail += f"\n[stopped after scanning {MAX_WALK_FILES} files; narrow the path]"
        return ToolResult("\n".join(shown) + tail)


def _scan(
    pattern: str, flags: int, files: list[str], root: str, glob: str | None, max_matches: int
) -> tuple[list[str], int]:
    regex = re.compile(pattern, flags)
    base = Path(root)
    matches: list[str] = []
    total = 0
    for name in files:
        f = Path(name)
        rel = display_path(base, f)
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
            # Only the first MAX_LINE_CHARS of a line are searched (minified files).
            if regex.search(line[:MAX_LINE_CHARS]):
                total += 1
                if len(matches) < max_matches:
                    matches.append(f"{rel}:{lineno}: {line[:300]}")
    return matches, total


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
        if glob is not None and not isinstance(glob, str):
            raise ToolError("glob must be a string")
        root = ctx.root.resolve()
        if start.is_file():
            files, truncated = [start], False
        else:
            files, truncated = _walk(root, start)
        # A pathological pattern like (a+)+$ can backtrack for hours inside one
        # re.search call, which Python cannot interrupt. Run the scan in a child
        # process so it can be killed at the deadline.
        ctx_mp = multiprocessing.get_context("spawn")
        with ctx_mp.Pool(1) as pool:
            job = pool.apply_async(
                _scan,
                (raw, regex.flags, [str(f) for f in files], str(root), glob, self.max_matches),
            )
            try:
                matches, total = job.get(timeout=SEARCH_TIMEOUT)
            except multiprocessing.TimeoutError:
                pool.terminate()
                raise ToolError(
                    f"search took longer than {SEARCH_TIMEOUT:g}s and was stopped; the pattern "
                    "may backtrack catastrophically (e.g. nested quantifiers) or the path is "
                    "too broad"
                ) from None
        if not matches:
            return ToolResult("(no matches)")
        more = total - len(matches)
        tail = f"\n[{more} more matches not shown]" if more else ""
        if truncated:
            tail += f"\n[stopped after scanning {MAX_WALK_FILES} files; narrow the path]"
        return ToolResult(truncate("\n".join(matches) + tail))
