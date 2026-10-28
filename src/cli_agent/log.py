"""Structured debug log: one JSON object per line, written only with --debug-log.

Records timing, token counts, retries, permission decisions, and tool outcomes.
It never records message text, tool arguments, tool output, or credentials
(permission reasons may name a user rule or a workspace path), so a log can be
attached to a bug report.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
from pathlib import Path
from typing import Any

_logger = logging.getLogger("cli_agent")


def event(name: str, **fields: Any) -> None:
    if _logger.isEnabledFor(logging.DEBUG):
        _logger.debug(json.dumps({"ts": round(time.time(), 3), "event": name, **fields}))


def enable(path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{path} is not a regular file")
    os.set_blocking(fd, True)
    os.fchmod(fd, 0o600)
    handler = logging.StreamHandler(os.fdopen(fd, "a", encoding="utf-8"))
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG)
    _logger.propagate = False
