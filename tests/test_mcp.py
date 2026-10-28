import json
import sys
from pathlib import Path

import pytest

from cli_agent.config import ConfigError, load_config
from cli_agent.mcp import ServerConfig, start_servers
from cli_agent.permissions import PermissionPolicy
from cli_agent.tools import ToolContext, ToolError

SERVER = str(Path(__file__).parent / "fixtures" / "mcp_echo_server.py")


@pytest.fixture
def echo(tmp_path: Path):  # type: ignore[no-untyped-def]
    servers, tools, problems = start_servers(
        [ServerConfig("echo", [sys.executable, SERVER])], str(tmp_path)
    )
    yield tools, problems
    for s in servers:
        s.close()


def test_tools_listed_across_pages_and_called(echo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tools, problems = echo
    assert problems == []
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"mcp__echo__echo", "mcp__echo__fail"}
    ctx = ToolContext(root=tmp_path)
    res = by_name["mcp__echo__echo"].run({"text": "hi"}, ctx)
    assert res.content == "echo: hi" and not res.is_error
    bad = by_name["mcp__echo__fail"].run({}, ctx)
    assert bad.is_error and "it broke" in bad.content
    assert by_name["mcp__echo__fail"].input_schema == {"type": "object", "properties": {}}


def test_mcp_tools_are_execute_effect(echo) -> None:  # type: ignore[no-untyped-def]
    tools, _ = echo
    assert all(t.effect == "execute" for t in tools)
    assert PermissionPolicy(mode="read-only").check(tools[0].name, "execute", {}).decision == "deny"
    assert PermissionPolicy(mode="auto-edit").check(tools[0].name, "execute", {}).decision == "ask"


def test_bad_server_reported_not_fatal(tmp_path: Path) -> None:
    servers, tools, problems = start_servers(
        [
            ServerConfig("nope", ["/nonexistent/server"]),
            ServerConfig("dies", [sys.executable, "-c", "pass"]),
        ],
        str(tmp_path),
    )
    assert servers == [] and tools == [] and len(problems) == 2


def test_server_exit_mid_session_is_tool_error(tmp_path: Path) -> None:
    servers, tools, _ = start_servers(
        [ServerConfig("echo", [sys.executable, SERVER])], str(tmp_path)
    )
    servers[0].proc.kill()
    servers[0].proc.wait()
    with pytest.raises(ToolError, match=r"exited|cannot write"):
        tools[0].run({"text": "x"}, ToolContext(root=tmp_path))
    servers[0].close()


def test_config_parsing(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text('[mcp_servers.fs]\ncommand = ["npx", "server"]\nenv = { ROOT = "/x" }\n')
    cfg = load_config(f, environ={})
    assert cfg.mcp_servers[0].command == ["npx", "server"] and cfg.mcp_servers[0].env == {
        "ROOT": "/x"
    }
    assert json.dumps(cfg.redacted())  # serializable, names only
    for bad in (
        '[mcp_servers.fs]\ncommand = "npx"\n',
        '[mcp_servers.fs]\ncommand = ["x"]\nfoo = 1\n',
        "mcp_servers = 3\n",
    ):
        f.write_text(bad)
        with pytest.raises(ConfigError):
            load_config(f, environ={})


def test_tool_name_collisions_skipped(tmp_path: Path) -> None:
    servers, tools, problems = start_servers(
        [
            ServerConfig("echo", [sys.executable, SERVER]),
            ServerConfig("echo", [sys.executable, SERVER]),
        ],
        str(tmp_path),
        reserved={"mcp__echo__fail"},
    )
    try:
        assert [t.name for t in tools] == ["mcp__echo__echo"]
        assert sum("already in use" in p for p in problems) == 3
    finally:
        for s in servers:
            s.close()
