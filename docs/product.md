# Product specification

## Vision

A small, trustworthy terminal coding agent. A developer states a task; the agent investigates the repository, edits files, runs the project's own commands to verify the result, and reports honestly what it did and did not verify. It works the same interactively and headless (CI, scripts), and its safety properties are enforced by code, not by asking the model nicely.

## Target users

- Individual developers working in a local git checkout who want a task done end-to-end (fix a failing test, add a flag, refactor a module) without leaving the terminal.
- Automation authors who want to run an agent non-interactively from scripts/CI with machine-readable output and predictable exit codes.

## Positioning

Compared with large agents (Claude Code, Codex CLI, Gemini CLI): fewer features, a small auditable codebase (pure Python, one runtime dependency), an explicit and testable permission model, and resumable sessions. Compared with minimal agents (mini-swe-agent): structured file tools with workspace confinement and a permission layer, so it is safe to point at a real repository.

## Non-goals (for 0.x)

- IDE integration, GUI, or a full-screen TUI.
- Multi-agent orchestration, background daemons, cloud execution.
- Web browsing / web search tools (would add an exfiltration channel; revisit with an explicit trust design).
- A plugin marketplace. Extensibility is through user-configured MCP servers (tools only).
- Sandboxing by default. The bubblewrap sandbox is opt-in (`--sandbox`) and Linux-only; without it shell commands run with the user's privileges, as the README states.

## Core user journeys

| ID | Journey |
|---|---|
| J1 | One-shot task: `cli-agent "fix the failing test"` → agent reads, edits, runs tests, prints summary, exits 0. |
| J2 | Interactive session: `cli-agent` → REPL, multiple prompts sharing context, approvals for risky actions. |
| J3 | Headless automation: prompt on stdin or argument, `--json` events, exit code reflects outcome, never blocks on input. |
| J4 | Recovery: Ctrl-C, crash, or API outage mid-task → session is saved; `--resume ID` / `--continue` picks up with a valid history. |
| J5 | Setup: install, set one env var, `cli-agent doctor` confirms it works or says exactly what is wrong. |

## Acceptance criteria

Each criterion names its verification. "CI" = automated test in `tests/` run by `scripts/check.sh`. "E2E" = scripted run against a real model (`scripts/e2e.sh`, local only; CI has no model access).

### Functional

- AC-F1 The agent loop sends tool results back to the model and continues until the model stops calling tools. (CI: `test_agent.py::test_tool_loop_end_to_end`)
- AC-F2 File tools: read with line numbers and paging; create/overwrite; exact-string edit with uniqueness check; list and regex search that skip VCS/venv dirs. (CI: `test_tools.py`)
- AC-F3 Shell tool returns exit code and combined output; non-zero exit is reported as an error result. (CI)
- AC-F4 One-shot, stdin, REPL, `--json`, `--continue`, `--resume`, `sessions`, `doctor`, `undo` work as documented. (CI: `test_cli.py`; REPL: `test_repl.py` via a pty)
- AC-F6 The user can revert the agent's file-tool changes per prompt without losing their own later edits. (CI: `test_checkpoint.py`)
- AC-F7 Tools from user-configured MCP stdio servers are usable and go through the permission policy as `execute`. (CI: `test_mcp.py`)
- AC-F5 Real model fixes a seeded bug in a small repo and the repo's tests pass afterwards. (E2E)

### Safety

- AC-S1 File tools reject paths outside the workspace, including via `..`, absolute paths, `~`, and symlinks. (CI)
- AC-S2 In `ask` mode with no terminal, no write or shell action executes. (CI: `test_non_interactive_ask_mode_denies_writes`)
- AC-S3 `read-only` mode cannot be widened by allow rules. (CI)
- AC-S4 Deny rules beat allow rules; built-in guard blocks catastrophic commands in every mode. (CI)
- AC-S5 Credential-like environment variables are not visible to shell commands. (CI)
- AC-S6 Repository content cannot change permissions: the only permission sources are the user config file, flags, and interactive approvals. (CI: config tests; design review)
- AC-S7 "Always allow" for a shell command allows exactly that command, not a glob. (CI)
- AC-S8 Secrets are never written to sessions or logs by the agent itself; config files refuse to hold API keys. (CI: config test; review)

### Reliability

- AC-R1 Retryable API failures (429, 5xx, 529, network, timeouts) are retried with backoff honouring `retry-after`; non-retryable ones fail fast with the API's message. (CI)
- AC-R2 Shell commands time out, and the whole process group is killed. (CI)
- AC-R3 Tool output is bounded (head+tail truncation); a tool exception becomes an error result, not a crash. (CI)
- AC-R4 Session history is saved after every turn atomically; an interrupted tool call is recorded so the resumed history is API-valid. (CI)
- AC-R6 Two cli-agent processes never write the same session: `--resume`, `--continue` and `/resume` refuse a session open in another live process (flock on `sessions/ID.lock`); if the lock can't be taken, the run continues with a notice. Known limitation: deleting a held lock file by hand lets a second process in. (CI: `test_a_session_open_elsewhere_is_not_resumed`, `test_resume_refuses_a_session_open_in_another_process`, lock unit tests)
- AC-R5 Long sessions stay within a context budget by eliding the oldest tool output, without the model losing facts it noted. (CI: unit; E2E: `long-context` task)
- AC-R6 Turn limit and `max_tokens` stops are reported with distinct exit code 3. (CI)

### Operability

- AC-O1 Exit codes: 0 done, 1 error, 2 usage/config, 3 incomplete, 130 interrupted, 129/143 SIGHUP/SIGTERM after a clean save. (CI)
- AC-O2 Errors are actionable (say what to set or run). (CI for config/credentials)
- AC-O3 Installable as a wheel with a working `cli-agent` entry point on Linux and macOS, Python 3.11 and 3.13. (CI matrix + build job)

## Release gates (0.1.0)

A version is releasable only when all of the following hold, recorded in `docs/progress.md`:

1. `scripts/check.sh` green on CI for every matrix entry on the release commit.
2. Every acceptance criterion above is either verified (with the named evidence) or explicitly listed as a known limitation in the release notes.
3. The E2E suite (`scripts/e2e.sh`) passed against a real model on the release commit, with the transcript summary recorded.
4. No open issue labelled `severity:high`.
5. An independent review (a separate reviewer agent given the diff, not the author's reasoning) of the security-relevant code (permissions, path confinement, shell, config) found no unresolved high-severity problem.
6. Wheel builds, installs into a clean venv, and `cli-agent --version` / `doctor` run.
