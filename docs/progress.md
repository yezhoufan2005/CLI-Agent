# Progress log

Newest entry first. Each entry: what changed, what was verified (with evidence), what is open, what to do next.

## 2026-10-28 — Release 0.2.0 gate evidence

| Gate | Evidence |
|---|---|
| 1. CI green | `main` at d94e5b6: success on all 5 jobs. The release workflow re-runs the gate on the tag |
| 2. ACs verified or limited | New AC-F6 and AC-F7 have CI tests. AC-R5 now has E2E evidence (`long-context`). Remaining limits are in the CHANGELOG |
| 3. E2E on the release commit | `scripts/e2e.sh claude-sonnet-4-6` at d94e5b6: `pass=11 fail=0` |
| 4. No open `severity:high` issue | Only #17 (`needs-human`, PyPI, optional) is open |
| 5. Independent review | Third review of everything since 0.1.0. All 14 findings fixed (PRs #29, #30) |
| 6. Wheel installs and runs | Checked locally before tagging, and again by the release workflow's smoke test |

## 2026-10-28 — Third independent review: fixes (PR #29)

The reviewer checked main at 32ec020 (undo, MCP, streaming, search subprocess) and reproduced 14 findings with scripts in /tmp/review3. Status:

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | high | The `spawn` resource tracker inherited the full environment, so `ANTHROPIC_API_KEY` was readable via /proc (regression of review-1 #10) | fixed: credential variables are removed from `os.environ` right after config load. CI test fails without the fix (checked) |
| 2 | high | MCP servers started with cwd = workspace, so a planted `json.py` or `node_modules` ran even in read-only mode | fixed: servers start in `$XDG_STATE_HOME/cli-agent/mcp-cwd` |
| 3 | med | Undo restored non-UTF-8 files with mangled bytes | fixed: checkpoints store exact original bytes (base64, format v2) and writes are byte-exact (CI) |
| 4 | med | Undo followed a symlink swapped in by the user | fixed: any symlink component means skip (CI) |
| 5 | med | An MCP timeout was reset by each notification; Ctrl-C during startup gave a traceback | fixed: one deadline per request; startup is inside try/finally and exits 130 |
| 6 | med | MCP memory was unbounded (notification flood) | fixed: only replies are queued, the queue is bounded, and lines are capped at 10 MB. Repro: RSS flat at 14 MB |
| 7 | med | Duplicate tool names, repeated cursors, unchecked schemas | fixed: collisions are skipped with a notice; repeated cursors stop pagination; schemas must be an object and ≤ 20k chars (CI) |
| 8 | med | `close()` left launcher children running | fixed: TERM then KILL the process group. Repro: 0 leftovers |
| 9 | med | Crash in a workspace whose path is not valid UTF-8 | fixed: `os.fsencode` |
| 10 | low | Empty prompts pushed real checkpoints out of the cap | fixed (CI) |
| 11 | low | Concurrent processes in one workspace lose checkpoints | fixed in PR #30: every change is load-modify-save under `flock`; per-process temp files; checkpoints tagged with an id and owner pid (CI: `test_two_processes_keep_each_others_checkpoints`) |
| 12 | low | A corrupt checkpoint file crashed every run | fixed: validated and ignored if wrong (CI) |
| 13 | low | Overflow retry fired on max_tokens-caused errors | fixed: those are excluded, and shrinking is capped at 4 rounds (CI) |
| 14 | low | OpenAI stream: repeated names, index-less fragments, in-stream context error marked retryable | fixed (CI) |

Also: MCP now answers server `ping` and rejects other server-to-client requests, so servers no longer stall waiting for a reply. Unverified suspicions that were not acted on:
- the 30 s search limit could misattribute slowness on very large trees
- no overall deadline for keepalive-only OpenAI streams

`./scripts/check.sh`: 211 passed.

## 2026-10-28 — Capability benchmark: Exercism Python (PR #28)

- `scripts/bench.py` gives the agent only an exercise's instructions and stub file, then copies in the hidden tests from [exercism/python](https://github.com/exercism/python) and runs pytest on them. Run with `--exercises <checkout>/exercises/practice`; local only, since it needs a real model.
- Run: exercism/python at `6105f6d`, 30 exercises sampled with seed 7, 6 in parallel, `--max-turns 30`. Reports are in `docs/bench/exercism-seed7-*.json`.
  - claude-haiku-4-5: 27/30 (90%). Failed: dot-dsl, house, pov.
  - claude-sonnet-4-6: 27/30 (90%). Failed: dot-dsl, house, kindergarten-garden.
- In every failure the agent reported `done` and exactly 1 hidden test failed. My guess, which I have not inspected, is that these exercises expect specific error messages or output formatting that the instructions don't fully specify.
- Caveats:
  - n=30 is small; ±5 percentage points is noise.
  - Exercism solutions are widely published, so models may have memorized them.
  - This is not comparable to SWE-bench.
  - The two models are not separated at this difficulty either.
  - Still, it is a reproducible end-to-end capability number for the agent loop, and it doesn't depend on tasks I wrote myself.

## 2026-10-28 — Dogfooding: cli-agent changed its own code (PR #23)

- Task given to `cli-agent --mode auto` (claude-sonnet-4-6) on a clone of this repo: add `--limit N` to `sessions` with a CLI test, and run `./scripts/check.sh`.
- Result: done in 5 turns (82k input tokens). The agent edited `cli.py` and `tests/test_cli.py` and ran the gate itself. Its summary honestly noted that the default cap of 50 was tested only indirectly and that it had not updated progress.md.
- Reviewed by hand (diff is idiomatic, negative values give exit 2). `check.sh` was re-run independently: 191 passed. Landed unchanged in PR #23.

## 2026-10-28 — Regex bound, undo (PRs #20–#21)

- PR #20 (B-REGEX-TIMEOUT): `search_files` runs in a spawned child process with a 30 s limit (CI: catastrophic `(a+)+$` returns an error in under 10 s). Costs about 25 ms per search. `__main__` gained an import guard, which spawn requires; covered by a CLI test of `python -m cli_agent` and checked manually from an installed wheel.
- PR #21 (B-UNDO): per-prompt file checkpoints. `cli-agent undo` and `/undo` restore originals and delete files the agent created. A file is skipped if its content hash no longer matches what the agent wrote, i.e. the user edited it afterwards. Paths in a tampered checkpoint file can't escape the workspace (CI). Shell-made changes are not tracked (documented).

Next: streaming for the OpenAI provider; dogfood cli-agent on this repo (real task, real model); MCP client (B-MCP) if time allows.

## 2026-10-28 — Context management verified end to end (PR #19)

- New E2E task `long-context`: read 8 files one at a time under `context_chars = 40000` (per-task `config.toml`, now supported by `e2e.sh`, which also always writes `.debug.jsonl`). `verify.sh` requires that compaction actually happened.
- First attempts failed (`exit=3`, 50 turns): after elision the model re-read the same files in a loop. Two causes:
  1. The old marker didn't say which call it replaced. Fixed: the marker names the tool and arguments.
  2. **The dev proxy drops assistant text that shares a message with a `tool_use` block.** Probe: the model's previous "The secret word is PELICAN" plus a tool call was quoted back as "NONE"; the same text without a tool call came back verbatim. So the notes the model wrote ("PART_0 = 343") never reached it. Fixed robustly: when a tool result is elided, the model's text that followed it is copied into the marker, because tool results always survive. Also a system-prompt line asks the model to note facts it will need.
- Verified (E2E): `long-context` PASS in 10 turns with 4 compactions. Full suite `pass=11 fail=0` on claude-sonnet-4-6 and claude-haiku-4-5. This closes AC-R5's "no real-model evidence" limitation.
- Overflow recovery: a non-retryable "prompt is too long" error compacts the history to half and retries once per elision round (CI: `test_context_overflow_*`). `context_chars` is configurable.
- Open (not ours to fix, unverified against the real API): via the dev proxy, prompt caching reports `cache_read 0` even for identical 8.9k-token requests. The proxy may not forward `cache_control`. Not reproducible against api.anthropic.com from here (no direct key).

## 2026-10-28 — 0.1.0 released

- https://github.com/yezhoufan2005/CLI-Agent/releases/tag/v0.1.0 has the wheel, the sdist and SHA256SUMS. It was built by the release workflow from 6599580.
- Process slip: the first `v0.1.0` tag was pushed before PR #16 merged, so it pointed at cdd45b3 (the wrong commit). Its release run failed at the gate, so nothing was published. The tag was deleted and re-created on the merged commit only after `main` CI passed. The cause was a test that extracted `Unreleased` release notes, which broke once that section was frozen; the test now checks the current version.
- Verified (manual): downloaded the release assets, `sha256sum -c` passed, installed the wheel into a clean venv (`cli-agent --version` → 0.1.0), and ran a real-model task with it (fixed `sq` in a scratch repo, checked with python3).
- Opened #17 (`needs-human`, optional): publishing to PyPI needs owner credentials. GitHub release install works without it.

Next candidates, in priority order:
1. Prompt caching showed `cache read 0` over a 4-turn real run. Find out whether the proxy or our request is at fault.
2. AC-R5: real long-session behaviour (compaction) has no evidence yet.
3. B-REGEX-TIMEOUT.
4. Streaming for the OpenAI provider.
5. B-BENCH.

## 2026-10-28 — Release 0.1.0 gate evidence

Commit under release: `cdd45b3` plus the version/CHANGELOG bump in PR #16.

| Gate | Evidence |
|---|---|
| 1. CI green on all matrix entries | CI run on `main` at cdd45b3: success (ubuntu/macos × 3.11/3.13 + build). The release workflow re-runs the gate on the tag. |
| 2. Each AC verified or listed as a limitation | All AC-F/S/R/O have CI tests. AC-F5 has E2E evidence. AC-R5 (real long-session behaviour) is listed under CHANGELOG "Known limitations". |
| 3. E2E on the release commit | `scripts/e2e.sh claude-sonnet-4-6` at cdd45b3: `pass=10 fail=0` |
| 4. No open `severity:high` issue | `gh issue list`: no open issues |
| 5. Independent security review | Two reviewer-agent passes. Every high/medium finding is fixed with a regression test (entries below). |
| 6. Wheel installs and runs | `uv build`, then install into a clean venv. `cli-agent --version` prints 0.1.0, and `cli-agent doctor` succeeded against the real model. |

## 2026-10-28 — Run 1, session 1: OpenAI provider, observability, sandbox, second review (PRs #10–#15)

- PR #10: OpenAI-compatible provider (`--provider openai`). Verified with contract tests only; the model proxy available here doesn't serve `/chat/completions`.
- PR #11: `--debug-log` (contains no message content; a CI test checks this with marker strings) and an end-of-run usage line.
- PR #12: three harder E2E tasks. 10/10 pass on both haiku-4-5 and sonnet-4-6, so the suite still doesn't separate the two models (→ B-BENCH).
  - CI failed because ruff formatted a fixture repo. Fix: `tests/e2e` excluded from ruff, plus a `.githooks/pre-commit` (run `scripts/setup-dev.sh`) so the static gate runs before every commit.
- PR #13: REPL driven through a pty in CI.
  - The macOS hang was the pty buffer filling while the test waited for exit. The test now drains the pty.
- PR #14: optional bubblewrap sandbox (`--sandbox auto|required`). It can't run in this container (no userns).
  - Linux CI installs bwrap and sets `CLI_AGENT_REQUIRE_SANDBOX_TESTS=1`. The 5 sandbox tests ran there and passed (163 passed on ubuntu-3.11).
  - The first CI run caught a wrong test assumption: `/tmp` is a private tmpfs inside the sandbox.
- PR #15: fixes from the second independent review. 9 findings were reproduced by the reviewer's scripts:
  1. `git status` at startup ran a `core.fsmonitor` program chosen by the repo, with the API key in its environment. Fixed: fsmonitor is disabled and the environment scrubbed. The regression test fails without the fix (checked).
  2. Malformed provider responses crashed the CLI with a traceback. Fixed: they now raise a retryable ProviderError for both providers.
  3. Lone surrogates crashed session save and stdout. Fixed: the session is written as ASCII JSON, and UI output escapes them.
  4. `_reap_orphans` stopped after 10 rounds. Fixed: it now loops until empty, with a 5 s bound.
  5. A NaN timeout disabled the timeout. Fixed with an `isfinite` check.
  6. Output past 4 MB lost its tail. Fixed: the head and tail are kept.
  7. Server payloads were quoted in errors and the log. Fixed: the error now gives only the size.
  8. Streamed partial text was duplicated on retry. Fixed: a "[partial response discarded]" marker is printed.
  9. `--debug-log` on a FIFO hung. Fixed with `O_NONBLOCK` plus a regular-file check.

  Also fixed: bidi and zero-width characters are now escaped in the UI.

  Not fixed (low; recorded):
  - `_reap_orphans` could kill children of other threads. Latent, since the CLI is single-threaded.
  - macOS doesn't reap setsid escapees. Documented as Linux-only.
  - The OpenAI parser synthesizes `call_N` ids.

## 2026-10-28 — Run 1, session 1: diffs, process limits, more E2E (PRs #5–#8)

- PR #5: tag-driven release workflow + CHANGELOG + `docs/release.md`. It has not run yet; it only runs on a tag push.
- PR #6: unified diff shown after each write/edit, and included in `--json` `tool_end.diff`. Display-only; the model never sees it. (CI)
- PR #7: `run_shell` kills leftover background jobs and setsid escapees on Linux, using `PR_SET_CHILD_SUBREAPER`, and caps output at 50 MB. (CI) The setsid test fails with reaping disabled (checked). Behaviour change: servers cannot outlive one command.
- PR #8: four new E2E tasks:
  - multi-file-rename
  - recover-failing-cmd
  - large-file (30k lines)
  - denied-action (a `--deny` rule must hold and the agent must say so)

  `scripts/e2e.sh` takes `E2E_FILTER` and per-task `args.txt`.
- Verified (E2E): `pass=7 fail=0` for claude-sonnet-4-6, and `pass=7 fail=0` for claude-haiku-4-5. In denied-action the agent reported the denial and did not try to delete the file another way. Caveat: the suite does not discriminate between these models yet, so it is a regression smoke test rather than a capability benchmark (→ B-E2E-HARD).

## 2026-10-28 — Run 1, session 1: main went red; fixed (PR #4)

- Defect: `test_shell_timeout_kills_process_group` failed intermittently on macOS (both 3.11 and 3.13, on `main` at fce946a and 7b232aa). Cause: on macOS `killpg` returns EPERM (not ESRCH) when the group contains only zombies; `_kill_group` caught only `ProcessLookupError`, so a timeout could crash the tool with PermissionError. Fix: treat any `OSError` from `killpg` as "nothing left to kill".
- Process failure: PR #3 was merged with `check (macos-latest, 3.13)` red because the ruleset required only a subset of jobs and allowed admin bypass. Fixed the mechanism, not just the instance: ruleset `main-protection` now requires all five CI jobs, requires a PR (squash only), and has **no bypass actors**; repo auto-merge enabled. Merge with `gh pr merge N --squash --auto`.

## 2026-10-28 — Run 1, session 1: streaming (PR #3)

- Anthropic adapter streams over SSE when the UI wants deltas (terminal UI); `--json` mode stays non-streaming. Handles `ping`, unknown events/deltas (ignored), mid-stream `error` (retryable for overloaded/api/rate-limit), stream cut before `message_stop` (retryable), malformed events (retryable), `model_context_window_exceeded` (→ max_tokens).
- Tool calls whose JSON arguments were cut off (max_tokens) are marked incomplete and answered with an error result instead of running with `{}`.
- Verified (CI): `tests/test_streaming.py` replays a real recorded stream (`tests/fixtures/anthropic_stream_tool_use.sse`: text + thinking + ping + tool_use with input_json_delta) plus synthetic edge cases; 133 tests pass.
- Verified (manual): real-model one-shot run streamed text to stdout; SIGINT at 4 s mid-stream → "interrupted", session saved with valid history.
- Verified (E2E): `scripts/e2e.sh` → `pass=3 fail=0`. Note e2e uses `--json`, i.e. the non-streaming path; real-model streaming is covered only by the manual run above.

Next: B-RELEASE (release workflow + CHANGELOG), then P1: B-GIT-SAFETY, B-DIFF, B-E2E-MORE, B-PROC-LIMITS.

## 2026-10-28 — Run 1, session 1: security review fixes (PR #2)

An independent reviewer agent (given the code and the AC-S* criteria, not the author's reasoning) reported 14 findings; the first five were reproduced with a script. Status:

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | high | Shell allow globs matched chained commands (`pytest; curl … \| sh`) | fixed: allow globs never match lines with shell metacharacters (CI: `test_shell_allow_glob_does_not_match_compound_commands`) |
| 2 | high | Path allow globs matched raw paths (`src/../.git/hooks/x` allowed by `src/*`) | fixed: subjects normalized via `resolve_in_root`; unresolvable paths never allowed, always denied by deny globs (CI: `test_path_rules_use_normalized_paths`) |
| 3 | med | Path deny rules bypassed with `./.env`, `sub/../.env` | fixed (same change) |
| 4 | low | Shell deny rules / guard bypassed by spacing, `&&`, `"$HOME"`, `--recursive`, `git -C . push --force`, `+main` | fixed: per-segment, whitespace-normalized matching; guard rewritten on shlex words (CI) |
| 6 | high | Atomic-write temp file followed a planted symlink | fixed: `mkstemp` + `fchmod` (CI: `test_atomic_write_ignores_planted_temp_symlink`) |
| 7 | high | `search_files` read through symlinks / special files | fixed: walk skips symlinks and non-regular files; read rejects non-regular files (CI) |
| 8 | med | AGENTS.md/CLAUDE.md read through symlinks | fixed: symlinked instruction files ignored (CI) |
| 9 | med | Approval prompt printed control characters raw; previews hid content | fixed: control chars escaped; `[v]iew full` option (CI: `tests/test_ui.py`) |
| 10 | med | Child shell could read agent's `/proc/$PPID/environ`; env denylist too narrow | fixed: `PR_SET_DUMPABLE=0` on Linux (CI test fails without it — checked); denylist widened. Credential files remain readable (documented) |
| 11 | med | `auto-edit` could write `.git/hooks`/`.git/config` | fixed: writes under `.git/` downgrade to ask unless an explicit path rule allows them (CI) |
| 12 | low | Regex backtracking in search | partial: matched text capped at 2000 chars/line; time bound → backlog B-REGEX-TIMEOUT |
| 13 | low | setsid/nohup children survive timeout; output spool unbounded; list walks whole tree | partial: walk capped at 20k files; rest → backlog B-PROC-LIMITS |
| 14 | low | Leftover session temp file kept loose permissions | fixed: `fchmod 0600` + `O_NOFOLLOW` (CI) |

Verified: `./scripts/check.sh` 122 tests pass; reviewer's repro script now prints ask/deny for every previously-allowed case.

Next: B-STREAM (streaming output), B-RELEASE, then P1 items. Also: `main` has a ruleset (no deletion/force-push; required checks `check (ubuntu-latest, 3.11)`, `check (macos-latest, 3.11)`, `build`; admins may bypass).

## 2026-10-28 — Run 1, session 1: bootstrap

Environment facts (checked): Debian arm64 container, Python 3.11.2, uv 0.11, gh authenticated with admin on `yezhoufan2005/CLI-Agent` (public), `main` unprotected at start. Model proxy reachable (`claude-sonnet-4-6`, `claude-haiku-4-5`, `claude-opus-4-8` accepted). Unprivileged user namespaces are **not** available here (`unshare -Ur` → EPERM), so bubblewrap cannot be tested in this container.

Done:
- Research summary with sources: `docs/research.md`.
- Spec, acceptance criteria, release gates: `docs/product.md`; architecture/decisions: `docs/architecture.md`; operating model; backlog; `CLAUDE.md`.
- Vertical slice (`src/cli_agent/`): agent loop, 6 tools, permission modes + rules + built-in guard, JSON sessions with repair/resume, Anthropic adapter (non-streaming, retries, prompt caching), terminal + JSON-lines UI, `sessions`/`doctor` subcommands.
- Quality gate `scripts/check.sh` (ruff, mypy --strict, pytest); CI workflow (ubuntu/macos × py3.11/3.13 + wheel build/install smoke).

Verified:
- `./scripts/check.sh`: 89 tests pass locally (CI).
- E2E `./scripts/e2e.sh claude-sonnet-4-6`: `pass=3 fail=0` (fix-bug, add-flag, refuse-injection). In refuse-injection the model read the hidden instruction in README.md, said it would not follow it, and completed the real task. (E2E)
