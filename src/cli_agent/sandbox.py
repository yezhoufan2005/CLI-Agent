"""Optional OS sandbox for shell commands (Linux, bubblewrap).

With the sandbox on, a command sees the host filesystem read-only, except
for the workspace (read-write) and a private /tmp. The user's home directory
is replaced by an empty tmpfs, so credential files such as ~/.ssh or
~/.aws are not visible. The network is off unless explicitly allowed. A
private PID namespace means every process the command starts dies with it.

Policy (config ``sandbox``):
- ``off``: run commands directly (default; same as before).
- ``auto``: sandbox if bubblewrap works here, otherwise warn and run directly.
- ``required``: refuse to run shell commands unless the sandbox works. Never
  silently falls back.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SandboxMode = Literal["off", "auto", "required"]
SANDBOX_MODES: tuple[SandboxMode, ...] = ("off", "auto", "required")


@dataclass(frozen=True)
class Sandbox:
    root: Path
    network: bool = False
    bwrap: str = "bwrap"

    def wrap(self, argv: list[str]) -> list[str]:
        root = str(self.root.resolve())
        cmd = [
            self.bwrap,
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",  # noqa: S108 - mounting a fresh tmpfs, not using /tmp
        ]
        home = os.path.expanduser("~")
        if home not in ("/", "") and os.path.isdir(home):
            cmd += ["--tmpfs", home]
        # Bound after the tmpfs mounts so a workspace under $HOME or /tmp stays visible.
        cmd += ["--bind", root, root]
        cmd += ["--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try"]
        if not self.network:
            cmd.append("--unshare-net")
        cmd += ["--die-with-parent", "--new-session", "--chdir", root, "--", *argv]
        return cmd


def probe(bwrap: str | None = None) -> str | None:
    """Return None if bubblewrap can create a sandbox here, else the reason it can't."""
    if not sys.platform.startswith("linux"):
        return "the sandbox needs Linux (bubblewrap)"
    exe = bwrap or shutil.which("bwrap")
    if not exe:
        return "bubblewrap (bwrap) is not installed; e.g. `sudo apt install bubblewrap`"
    try:
        r = subprocess.run(
            [
                exe,
                "--ro-bind",
                "/",
                "/",
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--unshare-pid",
                "--unshare-net",
                "--die-with-parent",
                "--",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return f"bubblewrap failed to start: {err}"
    if r.returncode != 0:
        detail = (r.stderr.strip().splitlines() or ["unknown error"])[-1]
        return f"bubblewrap cannot create a sandbox here: {detail}"
    return None


def resolve(mode: SandboxMode, root: Path, network: bool) -> tuple[Sandbox | None, str | None]:
    """Decide whether to sandbox. Returns (sandbox or None, message for the user).

    Raises SandboxUnavailable when mode is 'required' and the sandbox can't run.
    """
    if mode == "off":
        return None, None
    reason = probe()
    if reason is None:
        exe = shutil.which("bwrap") or "bwrap"
        net = "network allowed" if network else "no network"
        return Sandbox(root=root, network=network, bwrap=exe), f"shell sandbox on ({net})"
    if mode == "required":
        raise SandboxUnavailable(reason)
    return None, f"shell sandbox unavailable, commands run unsandboxed: {reason}"


class SandboxUnavailable(Exception):
    pass
