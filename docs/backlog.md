# Backlog

Priority: P0 = needed for 0.1.0 release gate, P1 = next, P2 = later. Status: `todo`, `doing`, `done`, `dropped`.
Pick the top `todo` whose dependencies are `done`. Add new items with a one-line acceptance check.

| ID | P | Status | Item | Acceptance check |
|---|---|---|---|---|
| B-SLICE | P0 | done | Vertical slice: loop, 6 tools, permissions, sessions, CLI, Anthropic adapter | `check.sh` green; e2e 3/3 (2026-10-28) |
| B-CI | P0 | done | CI green on GitHub for the matrix; protect `main` (require `check` + `build`) | Green run on `main`; ruleset active |
| B-REVIEW | P0 | done | Independent security review of permissions, path confinement, shell, config trust | Findings fixed or filed as issues (2026-10-28, see progress) |
| B-STREAM | P0 | done | Stream model output (SSE) so text appears as it is generated; Ctrl-C mid-stream cancels cleanly | Contract test with recorded SSE; manual REPL check |
| B-CLAUDE | P0 | done | Root `CLAUDE.md` for contributors/agents | File exists, < 60 lines |
| B-RELEASE | P0 | done | Release workflow: tag `v*` → build wheel/sdist, GitHub Release with notes; CHANGELOG | Dry-run release on a tag; install from release asset works |
| B-GIT-SAFETY | P1 | done | Warn at start when workspace is not a git repo or has uncommitted changes in write modes | CLI test |
| B-DIFF | P1 | done | Show a unified diff of each edit/write in the terminal UI (and in approval prompts) | UI unit test |
| B-E2E-MORE | P1 | done | More E2E tasks: multi-file change, recover from failing command, large file paging, denied action | e2e passes ≥ 90% over 2 runs |
| B-OPENAI | P1 | done | OpenAI-compatible Chat Completions provider | Contract tests; e2e via a compatible endpoint if available |
| B-LOG | P1 | done | `--debug` log file with request timing, retries, tool durations (no secrets, no content by default) | Test that log has no secret values |
| B-SANDBOX | P1 | done | Optional bubblewrap sandbox for `run_shell` (read-only root, writable workspace, no network option) | Tests on a host with userns (CI ubuntu?) ; skip with clear message otherwise |
| B-USAGE | P2 | done | Print token usage / estimated cost summary at end of run | CLI test |
| B-COMPACT | P2 | todo | Summarization compaction when elision is not enough | Long-session e2e. No evidence of need yet: `long-context` and every SWE-bench run fit with elision alone |
| B-MCP | P2 | done | MCP client for external tools (stdio) | Contract test with a toy server |
| B-PARALLEL | P2 | dropped | Run independent read-only tool calls concurrently | Dropped 2026-10-29: measured in a real E2E run (multi-file-rename), the 9 tool calls took 0.10 s in total against 9.7 s of model time, so concurrency would save under 1% and add risk to approval ordering |
| B-PROC-LIMITS | P1 | done | Kill processes that escape the shell's process group (setsid/nohup) at timeout; cap the on-disk output spool | Test: `setsid sleep` child is gone after timeout; spool ≤ limit |
| B-REGEX-TIMEOUT | P2 | done | Bound search_files time for pathological regexes (subprocess or deadline) | Test with `(a+)+$` on a long line returns within N s |
| B-E2E-HARD | P1 | done | Harder E2E tasks (symptom-only bug with hidden edge cases, spec-to-implementation with hidden tests, argparse refactor with golden output) | Added 3 tasks; 10/10 pass on haiku-4-5 and sonnet-4-6, so the suite still does not discriminate; next step is B-BENCH |
| B-BENCH | P2 | done | Run a public benchmark subset (e.g. 20 SWE-bench Lite/Verified instances) to measure capability against published numbers | Recorded pass rate with exact instance list and model |
| B-UNDO | P1 | done | Per-prompt file checkpoints with `cli-agent undo` / `/undo` | CI: tests/test_checkpoint.py, test_undo_subcommand |
| B-PASTE | P1 | done | REPL: multi-line paste is one prompt; `/` lines stay separate; typed-ahead input never answers approvals but is kept for the next prompt | CI: `test_repl.py` paste, typed-ahead and `/mode` tests |
| B-PROMPT-BENCH | P1 | done | Use kept SWE-bench transcripts to find agent-side failure causes and fix them in the system prompt | Seed 1+2 resolved counts and E2E recorded per change (docs/progress.md, 2026-10-29) |
| B-SHELL-EDITS | P1 | done | Agent changes file contents through file tools, so diffs and undo cover them | Shell source edits 9/20 → 3/20 SWE-bench runs; E2E 11/11 |
| B-LOCK-TIMEOUT | P2 | done | Checkpoint `flock` has no timeout; a stuck holder hangs file and shell tools until Ctrl-C | Test: a held lock makes the tool fail with a clear message within N s |
| B-HAIKU-COST | P2 | todo | Haiku's input tokens rose ~50% with the 0.4.0 prompt (more turns, more scratch scripts) | Same SWE-bench seeds with equal or better resolved count and lower total input tokens |
| B-SESSION-PICKER | P2 | todo | `/sessions` in the REPL and a numbered pick for `--resume` | CLI test |

