# Backlog

Priority: P0 = needed for 0.1.0 release gate, P1 = next, P2 = later. Status: `todo`, `doing`, `done`, `dropped`.
Pick the top `todo` whose dependencies are `done`. Add new items with a one-line acceptance check.

| ID | P | Status | Item | Acceptance check |
|---|---|---|---|---|
| B-SLICE | P0 | done | Vertical slice: loop, 6 tools, permissions, sessions, CLI, Anthropic adapter | `check.sh` green; e2e 3/3 (2026-10-28) |
| B-CI | P0 | todo | CI green on GitHub for the matrix; protect `main` (require `check` + `build`) | Green run on `main`; ruleset active |
| B-REVIEW | P0 | todo | Independent security review of permissions, path confinement, shell, config trust | Findings fixed or filed as issues |
| B-STREAM | P0 | todo | Stream model output (SSE) so text appears as it is generated; Ctrl-C mid-stream cancels cleanly | Contract test with recorded SSE; manual REPL check |
| B-CLAUDE | P0 | todo | Root `CLAUDE.md` for contributors/agents | File exists, < 60 lines |
| B-RELEASE | P0 | todo | Release workflow: tag `v*` → build wheel/sdist, GitHub Release with notes; CHANGELOG | Dry-run release on a tag; install from release asset works |
| B-GIT-SAFETY | P1 | todo | Warn at start when workspace is not a git repo or has uncommitted changes in write modes | CLI test |
| B-DIFF | P1 | todo | Show a unified diff of each edit/write in the terminal UI (and in approval prompts) | UI unit test |
| B-E2E-MORE | P1 | todo | More E2E tasks: multi-file change, recover from failing command, large file paging, denied action | e2e passes ≥ 90% over 2 runs |
| B-OPENAI | P1 | todo | OpenAI-compatible Chat Completions provider | Contract tests; e2e via a compatible endpoint if available |
| B-LOG | P1 | todo | `--debug` log file with request timing, retries, tool durations (no secrets, no content by default) | Test that log has no secret values |
| B-SANDBOX | P1 | todo | Optional bubblewrap sandbox for `run_shell` (read-only root, writable workspace, no network option) | Tests on a host with userns (CI ubuntu?) ; skip with clear message otherwise |
| B-USAGE | P2 | todo | Print token usage / estimated cost summary at end of run | CLI test |
| B-COMPACT | P2 | todo | Summarization compaction when elision is not enough | Long-session e2e |
| B-MCP | P2 | todo | MCP client for external tools (stdio) | Contract test with a toy server |
| B-PARALLEL | P2 | todo | Run independent read-only tool calls concurrently | Agent test |
