# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org); before 1.0, minor versions may change CLI flags, config keys, or the session format, and each such change is called out below.

## Unreleased

### Added
- `edit_file` falls back to a whitespace-tolerant match (indentation offset, trailing spaces, CRLF) when the exact text is not found and exactly one place matches; ambiguous matches are still refused.
- `cli-agent doctor` also checks bash, git, the state directory, shell-sandbox availability, and configured MCP server commands.

### Changed
- A reply cut off at the output token limit with no tool call no longer ends the run: the agent asks the model to continue (up to 3 times in a row) before stopping with exit code 3.

### Notes
- The OpenAI-compatible provider (Ollama, streaming and non-streaming) and the MCP client (`@modelcontextprotocol/server-filesystem`) have now been checked by hand against real servers.

## 0.2.0 — 2026-10-28

### Added
- `--max-session-tokens N` / `max_session_tokens`: stop (exit 3) once a session has used N input+output tokens.
- Streaming for the OpenAI-compatible provider.
- MCP client for stdio servers configured in the user config file (`[mcp_servers.NAME]`); tools are exposed as `mcp__NAME__TOOL` and always need approval outside `auto` mode.
- `sessions --limit N`.
- `cli-agent undo` and `/undo`: revert the file changes made by the agent's file tools during the last prompt (one prompt per undo, up to 20). Files changed by the user afterwards are left untouched.
- `search_files` runs in a child process with a 30 s limit, so a catastrophic regex cannot hang the agent.
- `context_chars` config key; on a "prompt is too long" error the agent compacts history and retries.

### Security
- Credential environment variables are removed from the agent's own environment after config load, so no child process (including multiprocessing helpers and MCP servers) inherits them.
- MCP servers start in a neutral directory instead of the workspace; server output is bounded; launcher child processes are killed on exit; colliding tool names and oversized schemas are rejected.
- Undo never follows symlinks and restores exact original bytes.

### Changed
- Elided tool output now names the call it replaced and repeats the note the model wrote after it, so long sessions don't loop re-reading files.

### Known limitations
- The OpenAI-compatible provider and the MCP client have only been tested against fakes, not real third-party endpoints or servers.

## 0.1.0 — 2026-10-28

### Added
- Agent loop with six tools: `read_file`, `list_files`, `search_files`, `write_file`, `edit_file`, `run_shell`.
- Permission modes `read-only`, `ask`, `auto-edit`, `auto`; allow/deny rules; built-in guard for catastrophic shell commands; `.git/` writes always ask.
- Resumable sessions (`--continue`, `--resume ID`, `cli-agent sessions`), saved atomically after every turn.
- Anthropic Messages API provider with streaming, retries (honouring `retry-after`), and prompt caching. Works with Anthropic-compatible proxies (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`).
- OpenAI-compatible Chat Completions provider (`--provider openai`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`); non-streaming.
- Unified diff shown after every file write/edit (`diff` field in `--json` `tool_end` events).
- Warning when the workspace is not a git repository or has uncommitted changes.
- Optional bubblewrap sandbox for shell commands (`--sandbox auto|required`): read-only host, writable workspace, hidden home directory, no network by default.
- `--debug-log FILE` (timings, tokens, decisions; no content) and an end-of-run usage line.
- Headless use: prompt from stdin, `--json` JSON-lines events, documented exit codes.
- `cli-agent doctor` to check configuration and connectivity.

### Security
- Workspace confinement for file tools (symlinks resolved; list/search skip symlinks).
- Credential-like environment variables are removed from shell commands; on Linux the agent is non-dumpable so commands cannot read its environment through `/proc`.
- Startup git check disables repository-configured programs (`core.fsmonitor`) and runs without credentials.
- Shell commands are not sandboxed by default; `--sandbox` is opt-in and Linux-only.

### Known limitations
- Context management only elides old tool output; it has unit tests but no real-model test on very long sessions.
- The OpenAI-compatible provider does not stream and has not been tested against a real endpoint (contract tests only).
- The E2E suite (10 tasks) passes on both claude-haiku-4-5 and claude-sonnet-4-6, so it is a regression check, not a capability benchmark.
- On macOS, background processes started with `setsid` can outlive a command (orphan reaping is Linux-only).
- Shell allow/deny rules and the built-in guard are best-effort pattern checks, not a security boundary. Use `--sandbox` for isolation.
