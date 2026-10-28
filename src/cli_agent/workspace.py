"""Workspace checks shown before a run that may modify files."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_warning(root: Path) -> str | None:
    """A warning if edits in `root` would be hard to review or undo, else None."""
    try:
        inside = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "git is not available; changes the agent makes cannot be reviewed or undone with git"
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return (
            f"{root} is not a git repository; changes the agent makes cannot be reviewed or "
            "undone with git"
        )
    try:
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    changed = [line for line in status.stdout.splitlines() if line.strip()]
    if changed:
        return (
            f"{len(changed)} tracked file(s) have uncommitted changes; consider committing "
            "first so the agent's edits are easy to review and undo"
        )
    return None
