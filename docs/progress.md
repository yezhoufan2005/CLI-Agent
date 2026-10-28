# Progress log

Newest entry first. Each entry: what changed, what was verified (with evidence), what is open, what to do next.

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
