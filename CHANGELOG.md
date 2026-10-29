# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org); before 1.0, minor versions may change CLI flags, config keys, or the session format, and each such change is called out below.

## Unreleased

## 0.6.0 — 2026-10-29

### Changed
- Releases are published to PyPI as `cli-agent-yzf` (`uv tool install cli-agent-yzf`); the name `cli-agent` is taken there. The command, module, and config/session directories are still `cli-agent`. Release wheels are now named `cli_agent_yzf-X.Y.Z-py3-none-any.whl`.

### Fixed
- Two cli-agent processes on the same session (e.g. `--continue` in two terminals, or `/resume` of a session still open elsewhere) overwrote each other's turns. `--resume`, `--continue` and `/resume` now refuse a session that another running cli-agent has open (exit code 2 for the flags; the REPL stays in its current session). Each process holds a lock file `sessions/ID.lock` while it has the session open.
- SIGTERM and SIGHUP (closing the terminal) now shut down like Ctrl-C: the running shell command is killed, the session is saved, and undo's shell-command count is written. The exit code is 128 + the signal number (143, 129). Before, the process died without cleanup. A second signal during that cleanup is ignored, `--json` runs still end with a `result` event, and MCP servers are all closed even if one fails.

### Notes
- Known limitation: deleting a session's `.lock` file by hand while cli-agent has the session open lets a second process open it.

## 0.5.0 — 2026-10-29

### Added
- REPL: `/sessions` lists this workspace's sessions (the current one marked `*`), and `/resume ID` switches to one of them without restarting.

### Changed
- The system prompt tells the agent to change file contents only with the file tools, not through shell commands, so every change is shown as a diff and can be undone. Deletes, moves and commands the user asks for still use the shell.
- `undo` says how many shell commands ran during the prompt it reverts, and reports a prompt that changed files only through the shell instead of skipping it. Shell-only prompts are dropped first when the 20-prompt limit is reached; empty prompts don't count toward it.
- An answer to an approval question other than y/n/a/v (or Enter) now means no, instead of asking again.

### Security
- Input typed before an approval question appears (while a command runs, or a partly typed `y` without Enter) can no longer answer that question. In the REPL that input is kept for the next prompt; a typed-ahead `/mode` that tightens permissions is applied before it, and one that loosens them is never applied from typed-ahead input. One-shot runs drop it and say what was dropped. Ctrl-C discards it.
- `--resume`, `/resume`, `--continue` and session listings ignore or refuse a session file whose stored id differs from its file name, which could overwrite another session.

### Fixed
- In non-interactive runs, a call that needed approval was reported to the model as "the user declined". It now says nobody can approve in this run, and names a shell allow rule (e.g. `run_shell:pytest*`) that matched the start of the command but not the whole line because of a pipe or redirection, if the plain command would be allowed. The policy itself is unchanged.
- The undo-checkpoint lock waits at most 10 s instead of forever; a write whose checkpoint can't be recorded still happens and says undo won't cover it.
- An `undo` that fails part-way keeps what it could not revert, lists what it did, and a retry finishes the same prompt.
- A failure to record shell-command counts no longer blocks the command; tampered counts are ignored; counts are no longer written on every command.

### Notes
- Measured on this release: E2E 11/11 with claude-sonnet-4-6 and claude-haiku-4-5; Exercism Python (30, hidden tests) 28/30 and 26/30; SWE-bench Lite sympy seed 1 with sonnet 15/20.
- Known limitation: if the process is killed (SIGHUP) within a second of several shell commands, undo may undercount them.

## 0.4.0 — 2026-10-29

### Added
- `--resume` accepts a unique prefix of a session id.
- REPL: a multi-line paste is sent as one prompt instead of one prompt per line. A line starting with `/` is always handled on its own, so a command typed while the agent is busy still applies.

### Changed
- The system prompt asks the agent to find and run the project's own test runner instead of skipping tests when pytest is missing, to fix the same defect in sibling code, and to fix (not just report) remaining wrong output that is part of the task.

### Fixed
- `--resume` matches the prefix literally (`*`, `?`, `[` are not glob patterns), rejects an empty or over-long id, and reports a non-session or unreadable file as an error instead of a traceback. `--resume` and `--continue` are mutually exclusive.
- REPL: Ctrl-D typed right after a prompt runs that prompt and then exits, instead of being swallowed.

### Notes
- SWE-bench Lite, sympy subset (Docker-free harness, two samples of 20): with the new system prompt claude-sonnet-4-6 resolved 15/20 (seed 1) and 14/20 (seed 2), claude-haiku-4-5 16/20 and 14/20, i.e. 29/40 and 30/40 (0.3.0+: 27/40 and 28/40). Single runs; the differences are within run-to-run noise. Not official SWE-bench numbers.

## 0.3.0 — 2026-10-29

### Added
- `edit_file` falls back to a whitespace-tolerant match (indentation offset, trailing spaces, CRLF) when the exact text is not found and exactly one place matches; ambiguous matches are still refused.
- `cli-agent doctor` also checks bash, git, the state directory, shell-sandbox availability, and configured MCP server commands.

### Changed
- A reply cut off at the output token limit with no tool call no longer ends the run: the agent asks the model to continue (up to 3 times in a row) before stopping with exit code 3.
- Streams fail after 5 minutes without content (keepalives don't count) instead of hanging; long streams that keep producing content are never cut.
- Python 3.14 is tested in CI.

### Notes
- SWE-bench Lite, sympy subset (20 instances, Docker-free harness `scripts/swebench_sympy.py`): 14/20 resolved with claude-sonnet-4-6, 15/20 with claude-haiku-4-5 (single runs; not official SWE-bench numbers).
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
