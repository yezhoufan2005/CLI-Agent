"""Permission policy: decides whether a tool call may run.

Decision order (first match wins):
1. Deny rules (user config, plus built-in guards for catastrophic shell commands).
2. Allow rules (user config, plus anything approved "always" during the session).
3. The mode's default for the tool's effect.

A rule is ``tool`` or ``tool:glob`` where the glob is matched against the
call's subject (the shell command, or the workspace-relative path).

This is a policy layer, not a sandbox: a shell command the user approves runs
with the user's privileges. The built-in guards only catch obvious mistakes.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["read-only", "ask", "auto-edit", "auto"]
MODES: tuple[Mode, ...] = ("read-only", "ask", "auto-edit", "auto")
Decision = Literal["allow", "deny", "ask"]

_MODE_DEFAULTS: dict[Mode, dict[str, Decision]] = {
    "read-only": {"read": "allow", "write": "deny", "execute": "deny"},
    "ask": {"read": "allow", "write": "ask", "execute": "ask"},
    "auto-edit": {"read": "allow", "write": "allow", "execute": "ask"},
    "auto": {"read": "allow", "write": "allow", "execute": "allow"},
}

# Commands that are almost never what the user wants from an agent. Denied in
# every mode, including auto; the user can still run them by hand.
_DANGEROUS_SHELL = [
    (
        re.compile(
            r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(-[a-zA-Z-]+\s+)*(/|~|\$HOME)(\s|/?\*?$|/?\s)"
        ),
        "recursive delete of / or the home directory",
    ),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "formatting a filesystem"),
    (re.compile(r"\bdd\b[^|;&]*\bof=/dev/(sd|nvme|hd|vd|disk|mmcblk)"), "writing to a raw disk"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"), "fork bomb"),
    (re.compile(r"\bgit\s+push\b[^|;&]*(--force\b|-f\b|--mirror\b)"), "force push"),
    (
        re.compile(r"(^|[;&|(]|\bsudo)\s*(shutdown|reboot|halt|poweroff)\b"),
        "shutting down the machine",
    ),
    (re.compile(r"\bchmod\s+(-[a-zA-Z]+\s+)*-R\s+[0-7]*777\s+/(\s|$)"), "recursive chmod of /"),
]


def dangerous_reason(command: str) -> str | None:
    for pattern, reason in _DANGEROUS_SHELL:
        if pattern.search(command):
            return reason
    return None


def call_subject(tool_name: str, args: dict[str, Any]) -> str:
    """The string a rule glob is matched against."""
    if tool_name == "run_shell":
        return str(args.get("command", ""))
    for key in ("path", "pattern"):
        if isinstance(args.get(key), str):
            return str(args[key])
    return ""


def _rule_matches(rule: str, tool_name: str, subject: str) -> bool:
    name, sep, glob = rule.partition(":")
    if name.strip() != tool_name:
        return False
    if not sep:
        return True
    return fnmatch.fnmatchcase(subject.strip(), glob.strip())


@dataclass
class Verdict:
    decision: Decision
    reason: str


@dataclass
class PermissionPolicy:
    mode: Mode = "ask"
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)

    def check(self, tool_name: str, effect: str, args: dict[str, Any]) -> Verdict:
        subject = call_subject(tool_name, args)
        if tool_name == "run_shell":
            reason = dangerous_reason(subject)
            if reason:
                return Verdict("deny", f"blocked by built-in guard: {reason}")
        for rule in self.deny:
            if _rule_matches(rule, tool_name, subject):
                return Verdict("deny", f"denied by rule {rule!r}")
        defaults = _MODE_DEFAULTS[self.mode]
        if self.mode == "read-only" and effect != "read":
            return Verdict("deny", "read-only mode: only read tools are allowed")
        for rule in self.allow:
            if _rule_matches(rule, tool_name, subject):
                return Verdict("allow", f"allowed by rule {rule!r}")
        decision = defaults.get(effect, "ask")
        return Verdict(decision, f"{self.mode} mode default for {effect} tools")

    def remember(self, tool_name: str, args: dict[str, Any]) -> str:
        """Record an 'always allow' approval; returns the rule that was added.

        Shell approvals are scoped to the exact command; file approvals to the tool.
        """
        if tool_name == "run_shell":
            # Escape glob metacharacters so the rule matches only this command.
            cmd = call_subject(tool_name, args).strip()
            escaped = re.sub(r"([*?\[])", r"[\1]", cmd)
            rule = f"run_shell:{escaped}"
        else:
            rule = tool_name
        if rule not in self.allow:
            self.allow.append(rule)
        return rule
