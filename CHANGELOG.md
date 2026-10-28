# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org); before 1.0, minor versions may change CLI flags, config keys, or the session format, and each such change is called out below.

## Unreleased

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
- Shell commands are not sandboxed (documented limitation).
