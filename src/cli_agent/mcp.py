"""Minimal MCP (Model Context Protocol) client for stdio servers.

Servers are configured only in the user's config file (never from the
workspace), started as child processes, and spoken to with newline-delimited
JSON-RPC 2.0. Their tools are exposed to the model as ``mcp__<server>__<tool>``.
Because an MCP tool can do anything, every MCP tool has the "execute" effect:
it asks in ask/auto-edit mode and is denied in read-only mode.

Only the tools capability is implemented (initialize, tools/list, tools/call).
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from cli_agent.tools.base import Effect, ToolContext, ToolError, ToolResult, truncate
from cli_agent.tools.shell import scrubbed_env

PROTOCOL_VERSION = "2025-06-18"
START_TIMEOUT = 30.0
CALL_TIMEOUT = 120.0
MAX_LINE_BYTES = 10_000_000
MAX_SCHEMA_CHARS = 20_000
MAX_QUEUED = 1000
_NAME_OK = re.compile(r"[^a-zA-Z0-9_-]")


class MCPError(Exception):
    pass


@dataclass
class ServerConfig:
    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)


class MCPServer:
    def __init__(self, cfg: ServerConfig, cwd: str):
        """`cwd` must not be the workspace: interpreters and package runners load
        code from their working directory (a planted json.py, node_modules)."""
        self.cfg = cfg
        env = scrubbed_env(dict(os.environ))
        env.update(cfg.env)  # explicitly configured values are passed even if secret-looking
        try:
            self.proc = subprocess.Popen(
                cfg.command,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as err:
            raise MCPError(f"cannot start MCP server {cfg.name!r}: {err}") from err
        self._next_id = 0
        # Only replies (messages with an id) are queued, and the queue is
        # bounded, so a server flooding notifications cannot exhaust memory.
        self._replies: queue.Queue[dict[str, Any] | None] = queue.Queue(MAX_QUEUED)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_LINE_BYTES:
                # Discard the rest of an oversized line.
                while line and not line.endswith(b"\n"):
                    line = self.proc.stdout.readline(MAX_LINE_BYTES)
                continue
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue  # servers sometimes log to stdout; ignore non-JSON lines
            if not isinstance(msg, dict) or "id" not in msg:
                continue  # notifications
            if "method" in msg:
                self._answer_server_request(msg)
                continue
            with contextlib.suppress(queue.Full):
                self._replies.put(msg, timeout=5)
        with contextlib.suppress(queue.Full):
            self._replies.put(None, timeout=5)

    def _answer_server_request(self, msg: dict[str, Any]) -> None:
        """Reply to server-to-client requests so servers don't stall on them."""
        if msg.get("method") == "ping":
            reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
        else:
            reply = {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "error": {"code": -32601, "message": "method not supported"},
            }
        with contextlib.suppress(MCPError):
            self._send(reply)

    def request(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise queue.Empty
                msg = self._replies.get(timeout=remaining)
            except queue.Empty:
                raise MCPError(
                    f"{self.cfg.name}: no reply to {method} within {timeout:g}s"
                ) from None
            if msg is None:
                raise MCPError(f"{self.cfg.name}: server exited")
            if msg.get("id") != rid:
                continue  # a late reply to an earlier, timed-out request
            if "error" in msg:
                err = msg["error"] if isinstance(msg["error"], dict) else {}
                raise MCPError(f"{self.cfg.name}: {err.get('message', 'error')}")
            return msg.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
            self.proc.stdin.flush()
        except OSError as err:
            raise MCPError(f"{self.cfg.name}: cannot write to server: {err}") from err

    def start(self) -> list[dict[str, Any]]:
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "cli-agent", "version": _version()},
            },
            START_TIMEOUT,
        )
        self.notify("notifications/initialized")
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(50):
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params, START_TIMEOUT)
            if not isinstance(result, dict):
                raise MCPError(f"{self.cfg.name}: malformed tools/list result")
            listed = result.get("tools")
            if isinstance(listed, list):
                tools.extend(t for t in listed if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                break
            seen.add(cursor)
        return tools

    def close(self) -> None:
        """Stop the server and anything it started (launchers like npx or sh -c)."""
        with contextlib.suppress(OSError):
            if self.proc.stdin:
                self.proc.stdin.close()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # TERM the whole group, give it a moment, then KILL whatever is left:
        # the leader exiting says nothing about its children.
        with contextlib.suppress(OSError):
            os.killpg(self.proc.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=1)
        with contextlib.suppress(OSError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=2)


class MCPTool:
    effect: Effect = "execute"

    def __init__(self, server: MCPServer, spec: dict[str, Any]):
        self.server = server
        self.remote_name = str(spec.get("name", ""))
        self.name = (
            f"mcp__{_NAME_OK.sub('_', server.cfg.name)}__{_NAME_OK.sub('_', self.remote_name)}"[:64]
        )
        desc = str(spec.get("description") or "")[:2000]
        self.description = f"[MCP server {server.cfg.name}] {desc}".strip()
        schema = spec.get("inputSchema")
        # Providers require an object schema; an oversized one would also bypass
        # the description cap and flood the prompt.
        ok = (
            isinstance(schema, dict)
            and schema.get("type") == "object"
            and len(json.dumps(schema)) <= MAX_SCHEMA_CHARS
        )
        self.input_schema: dict[str, Any] = (
            schema if ok and isinstance(schema, dict) else {"type": "object", "properties": {}}
        )

    def summarize(self, args: dict[str, Any]) -> str:
        return f"{self.server.cfg.name}.{self.remote_name} {json.dumps(args)[:200]}"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            result = self.server.request(
                "tools/call", {"name": self.remote_name, "arguments": args}, CALL_TIMEOUT
            )
        except MCPError as err:
            raise ToolError(str(err)) from err
        if not isinstance(result, dict):
            raise ToolError(f"{self.server.cfg.name}: malformed tools/call result")
        parts = []
        for item in result.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(f"[{item.get('type', 'unknown')} content omitted]")
        if not parts and "structuredContent" in result:
            parts.append(json.dumps(result["structuredContent"]))
        return ToolResult(truncate("\n".join(parts)), is_error=bool(result.get("isError")))


def start_servers(
    configs: list[ServerConfig], cwd: str, reserved: set[str] | None = None
) -> tuple[list[MCPServer], list[MCPTool], list[str]]:
    """Start every server; a failing server is reported, not fatal.

    Tool names that collide with `reserved` or with an earlier tool are skipped.
    """
    taken = set(reserved or ())
    servers: list[MCPServer] = []
    tools: list[MCPTool] = []
    problems: list[str] = []
    for cfg in configs:
        try:
            server = MCPServer(cfg, cwd)
        except MCPError as err:
            problems.append(str(err))
            continue
        try:
            specs = server.start()
        except MCPError as err:
            problems.append(f"MCP server {cfg.name!r} failed to start: {err}")
            server.close()
            continue
        servers.append(server)
        for spec in specs:
            if not spec.get("name"):
                continue
            tool = MCPTool(server, spec)
            if tool.name in taken:
                problems.append(f"MCP tool {tool.name} skipped: name already in use")
                continue
            taken.add(tool.name)
            tools.append(tool)
    return servers, tools, problems


def parse_server_configs(raw: object, source: str) -> list[ServerConfig]:
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: mcp_servers must be a table")
    out = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ValueError(f"{source}: mcp_servers.{name} must be a table")
        command = spec.get("command")
        if not (isinstance(command, list) and command and all(isinstance(c, str) for c in command)):
            raise ValueError(f"{source}: mcp_servers.{name}.command must be a list of strings")
        env = spec.get("env", {})
        if not (isinstance(env, dict) and all(isinstance(v, str) for v in env.values())):
            raise ValueError(f"{source}: mcp_servers.{name}.env must map names to strings")
        unknown = set(spec) - {"command", "env"}
        if unknown:
            raise ValueError(f"{source}: mcp_servers.{name}: unknown key(s) {sorted(unknown)}")
        out.append(ServerConfig(name=str(name), command=list(command), env=dict(env)))
    return out


def _version() -> str:
    from cli_agent import __version__

    return __version__
