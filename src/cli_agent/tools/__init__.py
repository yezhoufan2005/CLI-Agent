"""Built-in tool registry."""

from __future__ import annotations

from cli_agent.tools.base import Tool, ToolContext, ToolError, ToolResult, spec_of
from cli_agent.tools.files import EditFile, ListFiles, ReadFile, SearchFiles, WriteFile
from cli_agent.tools.shell import RunShell

__all__ = ["Tool", "ToolContext", "ToolError", "ToolResult", "default_tools", "spec_of"]


def default_tools() -> list[Tool]:
    return [ReadFile(), ListFiles(), SearchFiles(), WriteFile(), EditFile(), RunShell()]
