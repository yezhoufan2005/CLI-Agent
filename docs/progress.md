# Progress log

Newest entry first. Each entry: what changed, what was verified (with evidence), what is open, what to do next.

## 2026-10-29 — Run 1, session 3: Ctrl-C during `/undo` lock wait (PR #73)

- Confirmed the session-2 suspicion: Ctrl-C while `/undo` waited (up to 10 s) for another session's checkpoint lock left the REPL with exit 130. Now it prints "undo cancelled" and the REPL continues; SIGTERM/SIGHUP still exit. CI: `test_ctrl_c_during_undo_lock_wait_keeps_the_repl` (fails without the change; checked).
- Checked the PR #72 suspicion (dangling tool_use on disk after a second signal): not a defect. `Agent.run` calls `Session.repair` before every request, and it is the only code that sends history to the provider (the `doctor` ping uses a fresh message); covered by `test_session_repair_on_resume`.
- Still open: two REPLs on one session overwrite each other. Next: B-HAIKU-COST.

## 2026-10-29 — Tenth (focused) review of #71: fixes (PR #72)

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | med | A second SIGHUP during the shell kill skipped SIGKILL, leaving a TERM-ignoring command running | fixed: only the first signal raises. Repro `r2_double`: `late exists after 8s: False` |
| 2 | med | A second signal while closing MCP servers gave a traceback, exit 1, orphaned server | fixed: same, and each server is closed even if another fails. Repro `r4_mcp`: no surviving server |
| 3 | low | SIGTERM before `main`'s `try` (e.g. while reading stdin) gave a traceback | fixed: handler installed just before the guarded block; earlier, the default action applies (repro: exit -15, no traceback) |
| 4 | low | `--json` runs ended without a `result` event on SIGTERM | fixed (CI: `test_sigterm_in_json_mode_still_emits_a_result_event`, which also sends a second SIGTERM) |

Unverified: a second signal during the save inside `Agent.run` could leave a dangling tool_use on disk; `load`/`--resume` should call `repair` (check next session).

## 2026-10-29 — Clean exit on SIGTERM/SIGHUP (PR #71)

- Fixes the review-7 #7 limitation and next-step 2 of the 0.5.0 entry. SIGTERM/SIGHUP raise `Terminated` (a `KeyboardInterrupt` subclass), so the existing Ctrl-C cleanup runs (kill the command's process group, repair and save the session, write checkpoint counts in `finally`), then the process exits with 128+signal even from the REPL.
- CI: `test_sighup_kills_the_command_and_keeps_undo_count` (3 shell commands, SIGHUP during the third: exit 129, the `sleep 30; touch late` never finishes, undo reports 3 commands, session saved). Fails without the change.
- Reviewed in the entry above (four findings, all fixed). Next: B-HAIKU-COST.

## 2026-10-29 — 0.5.0 released

- https://github.com/yezhoufan2005/CLI-Agent/releases/tag/v0.5.0 was built by the release workflow from 30422cd after `main` CI passed. Verified: downloaded the assets, `sha256sum -c` ok, clean-venv install reports `cli-agent 0.5.0`.
- Session 2 of this run ended here (deadline 05:51 UTC). Next steps are in the gate-evidence entry below.

## 2026-10-29 — Release 0.5.0 gate evidence; state for the next session

| Gate | Evidence |
|---|---|
| 1. CI green | `main` at 1768ebd: all jobs green. The release workflow re-ran the gate on the tag (30422cd) |
| 2. ACs | No new ACs. New behaviour (typeahead handling, shell counts in undo, lock timeout, `/sessions`, `/resume`, headless approval messages) has CI tests in `test_repl.py`, `test_checkpoint.py`, `test_cli.py`, `test_agent.py`, `test_permissions.py` |
| 3. E2E | `scripts/e2e.sh` at 1768ebd: claude-sonnet-4-6 `pass=11 fail=0`, claude-haiku-4-5 `pass=11 fail=0` (the release commit only changes version strings and docs) |
| 4. No open `severity:high` | None; only #17 (optional PyPI, needs-human) |
| 5. Independent review | Reviews 6–9 covered all code since 0.4.0; every reproduced finding is fixed except review 7 #7 (SIGHUP undercount), a known limitation. The small #67 fix was not reviewed again |
| 6. Wheel installs | Release workflow smoke test, then a manual install from the release asset (entry above) |

Next session, highest value first:
1. B-HAIKU-COST: haiku uses ~50% more input tokens since 0.4.0. Use `scripts/swebench_sympy.py --keep` transcripts to find avoidable scratch-script turns.
2. Clean shutdown on SIGHUP/SIGTERM (save session, finish checkpoints): fixes the remaining undercount.
3. Unverified review suspicions: Ctrl-C during the 10 s lock wait in `/undo` may exit the REPL; two REPLs on one session overwrite each other.

Process note: progress entries for PRs #61–#67 were first written with `str.replace` anchored on a heading that was not yet on `main`, so they silently did not land; restored in PR #70. Insert at the top of the file instead.

## 2026-10-29 — Ninth (focused) review of PR #66: fixes (PR #67)

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | med | The session id check was only in `load()`; `--continue` uses `list()`, so a copied session file still overwrote the original | fixed: `list()` skips files whose stored id ≠ file name (CI: `test_continue_skips_a_session_file_naming_another_id`) |
| 2 | med (regression from #66) | `_trim` protected an empty live checkpoint and evicted two file checkpoints | fixed: empty checkpoints don't count toward the cap; shell-only ones go first (CI: `test_full_stack_of_file_changes_keeps_all_of_them`) |
| 3 | low | A leading typed-ahead loosening `/mode` was still applied | fixed: never applied from typed-ahead input (CI) |

Known limitation: with 20 file-change prompts held, a newer shell-only prompt is evicted first, so undo goes past it silently. It holds no data.

## 2026-10-29 — Eighth independent review: fixes (PR #66)

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | med | Since #65 a typed-ahead `/mode read-only` after text was joined into the prompt and never applied | fixed: tightening typed-ahead `/mode` lines are applied first; loosening ones dropped with "type it again" (CI) |
| 2 | med | A failed `undo` dropped the checkpoint and lost originals; the retry reverted an older prompt | fixed: unreverted files are kept, the error lists what was reverted (CI) |
| 3 | low-med | A copied session file with another `id` inside overwrote that session on `/resume`/`--resume` | fixed in `load()` (and in `list()` by #67) |
| 4 | low | `_trim` evicted the newest shell-only prompt | changed again in #67 |
| 5 | low | `/resume` printed the title unescaped | fixed |
| 6 | low | After a failed `begin`, records went into the previous checkpoint | fixed |

Unverified: with a stuck lock every `run_shell` waits 10 s.

## 2026-10-29 — Seventh independent review: fixes (PR #65)

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | high (regression of #56) | A `y` typed without Enter stayed in the tty line buffer; Enter at the question answered `y` | fixed: drain, then `tcflush` (CI: `test_partly_typed_answer_does_not_carry_into_the_approval`) |
| 2 | med | Lines kept at an approval ran as a prompt after Ctrl-C | fixed: Ctrl-C / interrupted runs clear the queue |
| 3 | med | A kept paste was split at `/` lines, so `/mode auto` inside it ran | fixed: same rule as live input (CI) |
| 4 | med | Shell-only prompts pushed file edits out of the undo cap | fixed (CI) |
| 5 | med, pre-existing | A >64 KB or still-arriving paste fed answers to the approval; a `y` line approved | fixed: read until quiet 0.2 s (max 2 s), cap, flush; unrecognized answer = no |
| 6 | low | The allow hint matched through `sudo`/`env`/`VAR=` and ignored deny rules | fixed: only if `check()` on the plain command allows (CI) |
| 7 | low | SIGHUP within a second of several shell commands undercounts them | partly: count also saved every ≥1 s. Known limitation |

## 2026-10-29 — Checkpoint lock timeout and REPL sessions (PRs #63, #64)

- B-LOCK-TIMEOUT: the checkpoint `flock` blocked forever. Repro (held lock, then `write_file`): main hung until `timeout 5` killed it; with the fix it returns at once with a note that undo won't cover the write. Polled with a 10 s deadline (CI: `test_held_lock_fails_fast_and_the_write_still_happens`).
- B-SESSION-PICKER: `/sessions` and `/resume ID` in the REPL (CI: `test_sessions_list_and_resume_inside_the_repl`).

## 2026-10-29 — Sixth independent review: fixes (PR #61)

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | med | The approval `tcflush` discarded a `/mode read-only` typed during a run | fixed: pending input is read, never used as the answer, and kept for the next REPL prompt (CI) |
| 2 | low-med | `undo` was silent about shell-only prompts | fixed (CI) |
| 3 | low | A failing `note_shell` blocked the command | fixed: logged, command runs; counts validated (CI) |
| 4 | low | Every shell command rewrote the checkpoint file (294 ms per call at 107 MB) | fixed: written on the first command and at the end of the run (CI) |

## 2026-10-29 — Exercism rerun on the 0.4.0+ prompt

- Cross-check that the prompt changes (#53, #57) were not tuned to sympy only: Exercism Python, same 30 exercises (seed 7, exercism/python `6105f6d`), main at the file-tools rule.
  - claude-sonnet-4-6: 28/30 (0.2.0: 27/30). Failed: house, pov.
  - claude-haiku-4-5: 26/30 (0.2.0: 27/30). Failed: dot-dsl, house, pov, robot-simulator.
- Both are within ±1 of the 0.2.0 numbers, i.e. no measurable change on a second benchmark. Reports: `docs/bench/exercism-seed7-*-v2.json`.

## 2026-10-29 — Headless approvals explain allow-rule misses (PR #59)

- Found by a fresh-user check of the 0.4.0 wheel (`uv tool install` from the release, `doctor`, one-shot fix in a scratch git repo with `--mode auto-edit --allow 'run_shell:python*' --allow 'run_shell:pytest*'`, stdin not a tty). The model ran `python -m pytest -q 2>&1 | tail -30`. Shell allow rules deliberately never match command lines with operators, so it needed approval. Nobody could approve, the model was told "the user declined", and it finished without running the tests (3/3 runs on 0.4.0).
- Fix: `approve` returns `unattended` when there is no terminal, and the tool result says the run is non-interactive. When a `run_shell` allow rule matches the first simple command, the result and a notice name the rule and explain that operators prevent the match. Permission decisions are unchanged.
- Verified (E2E-style, manual): the same scenario on the branch, 3/3 runs: the piped command was refused, then the model ran plain `python -m pytest -q`, which the rule allowed, and reported the passing test. CI: `test_unattended_approval_explains_and_hints_at_operator_rule`, `test_operator_hint_only_when_a_rule_matches_the_first_command`.
- Also seen: `doctor` reports the dev proxy answering `claude-sonnet-4-6` requests as `claude-sonnet-5.5` and `claude-haiku-4-5` as `claude-haiku-5.5`. All benchmark numbers in this repo are labelled with the requested name; the model behind the proxy is whatever it maps that name to.

## 2026-10-29 — Cost of the 0.4.0 prompt; docs refresh

- Cost check from the kept reports (input tokens summed over 20 instances; no prompt caching through the dev proxy, so every turn resends the history):

  | Run | Resolved | Total input tokens | Median turns |
  |---|---|---|---|
  | sonnet seed 1, 0.3.0 prompt (v2) | 14/20 | 2.84M | 9 |
  | sonnet seed 1, 0.4.0 prompt (v3) | 15/20 | 1.90M | 9 |
  | sonnet seed 1, + file-tools rule (v4) | 15/20 | 2.58M | 9 |
  | haiku seed 1, 0.3.0 prompt (v2) | 15/20 | 7.93M | 14 |
  | haiku seed 1, 0.4.0 prompt (v3) | 16/20 | 11.96M | 22 |
  | haiku seed 2, 0.3.0 → 0.4.0 | 13 → 14/20 | 3.51M → 5.22M | 11 → 14 |

  Sonnet's cost did not rise. Haiku's rose about 50%, from more turns: it now runs tests and more scratch scripts (in seed 1, 366 shell calls and 68 of them test runs). Exact repeated tool calls are rare (≤4 per 20-instance run), so this is not looping. Accepted for now: haiku is several times cheaper per token than sonnet, and the change resolved one more instance on each seed. Revisit if a user-facing cost cap (`--max-session-tokens`) is hit in practice.
- docs/architecture.md: D5 was stale (it said the sandbox could not be tested); it now describes the opt-in bubblewrap sandbox. New D10 records the file-tools rule and why it is prompt-level.

## 2026-10-29 — File changes only through file tools (PR #57)

- Observation from the kept SWE-bench transcripts: in 9 of 20 seed-1 sonnet runs the agent edited library source through `run_shell` (python heredocs with `open(p,'w')`, `sed -i`). Those changes bypass the diff display, the `.git/` write guard's file-tool path, and `undo` checkpoints.
- Change: the system prompt says to change file *contents* only with `edit_file`/`write_file`, and to use `run_shell` for deleting/moving files or when the user asks for a specific command. `undo` now says how many shell commands ran in the prompt it reverts (their changes are not tracked).
- First wording ("never through run_shell") made sonnet refuse `rm app.py` in the `denied-action` E2E task (1 FAIL in 2 suite runs; it explained the rule instead of trying the command). The narrowed wording passed that task 3/3.
- Measured, sonnet seed 1 (same 20 instances): shell source edits in 9/20 runs before, 3/20 after (narrowed wording; 1/20 with the first wording). Resolved 15/20 in both, so no capability cost. E2E with the final wording: sonnet `pass=11 fail=0`, haiku `pass=11 fail=0`. Report: `docs/bench/swebench-lite-sympy-seed1-claude-sonnet-4-6-v4.json`.
- CI: `test_undo_reports_shell_commands_it_cannot_revert`, `test_undo_mentions_shell_commands`.

## 2026-10-29 — 0.4.0 released; approval typeahead (PR #56)

- https://github.com/yezhoufan2005/CLI-Agent/releases/tag/v0.4.0 was built by the release workflow from 8eb6eae after `main` CI passed. Verified: downloaded the assets, `sha256sum -c` ok, clean-venv install reports `cli-agent 0.4.0`.
- PR #56 (from the fifth review's unverified suspicion): approval prompts now flush terminal input typed before the question was shown and say so. Before, a `y` typed while a command ran approved the next command unseen. CI: `test_input_typed_ahead_does_not_answer_an_approval` fails without the fix.

## 2026-10-29 — Release 0.4.0 gate evidence

| Gate | Evidence |
|---|---|
| 1. CI green | `main` at 2f19052: all jobs green. The release workflow re-runs the gate on the tag |
| 2. ACs | No new ACs. New behaviour (paste joining, typed-ahead commands, Ctrl-D, `--resume` edge cases) is covered by CI tests in `test_repl.py` and `test_cli.py` |
| 3. E2E | `scripts/e2e.sh claude-sonnet-4-6` at 2f19052 (the release commit differs only in version strings, changelog and docs): `pass=11 fail=0` |
| 4. No open `severity:high` | None open; only #17 (optional PyPI, needs-human) |
| 5. Independent review | Fifth review covered all code since 0.3.0; all 5 findings fixed (#54) |
| 6. Wheel installs | Release workflow smoke test, then a manual install from the release asset (recorded after tagging) |

Haiku with the new prompt (PR #53): seed 1 16/20 (was 15/20 v2), seed 2 14/20 (was 13/20). Combined with sonnet: sonnet 29/40, haiku 30/40, up from 27/40 and 28/40. Each model and seed moved by +1; still within noise, but all four moved the same way. Reports: `docs/bench/swebench-lite-sympy-seed{1-*-v3,2-*-v2}.json`.

## 2026-10-29 — Fifth independent review: fixes (PR #54)

A reviewer agent checked v0.3.0..main (prefix `--resume`, REPL paste joining, prompt text) and reproduced 5 findings (scripts in /tmp/review5, not kept). Status:

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | med | A `/mode read-only` typed while a run was going was joined with the next typed line and rejected as bad usage, so the next prompt ran with the looser mode | fixed: a line starting with `/` is never joined (CI: `test_command_typed_during_a_run_still_applies`) |
| 2 | low | Ctrl-D right after Enter was read as a paste continuation and swallowed | fixed: the prompt runs, then the REPL exits (CI) |
| 3 | low | `--resume` prefixes were glob patterns (`*bbb` matched) and `**` gave a confusing error | fixed: literal `startswith` (CI) |
| 4 | low, pre-existing | A 300-char id, a directory named `x.json`, or a non-session `[]` file crashed with a traceback | fixed: ids > 100 chars are invalid; load errors become a clean exit-2 message (CI) |
| 5 | low, pre-existing | `--resume ''` silently started a new session; `--resume X --continue` silently ignored `--continue` | fixed: empty id is rejected; the flags are mutually exclusive (CI) |

Also from the review: the new prompt line about using the project's own test runner now says "unless running tests was denied", so it can't be read as license to work around a denial. Unverified suspicions, not acted on: macOS libedit read-ahead could defeat the 50 ms paste check; a paste arriving in chunks >50 ms apart over ssh could be partly read by an approval prompt (pre-existing class: approval reads whatever is on stdin).

## 2026-10-29 — SWE-bench failure analysis; system prompt change (PRs #50–#53)

- PR #50: fixed the flaky `test_repl_decline_and_mode_switch` that turned `main` red after #48. readline prints the prompt before it leaves canonical mode, so an early Ctrl-D arrived as NUL. The test now repeats Ctrl-D until exit (9/15 local failures before, 12/12 passes after).
- PR #51: `scripts/swebench_sympy.py --keep DIR` saves each instance's events, stderr and diff.
- Failure analysis, sonnet, the 14 instances unresolved in either seed-1 v2 or seed-2. Agent-side causes seen in the transcripts:
  - **Tests not run.** In 10 of 13 transcripts the agent said it did not run the existing tests, usually because pytest is not installed in the venv. sympy's own `bin/test` was never tried.
  - **Sibling defects missed.** In 12454 the same out-of-range loop exists in `_eval_is_upper_hessenberg`; in 11400 the agent noticed `Ne(x, 0)` is invalid C and reported it instead of fixing it.
  - The rest look like spec ambiguity that hidden tests decide (13043 set vs list, 13177 `is_positive`), which the agent can't see.
- Change (PR #53): the system prompt asks the agent to discover and run the project's own test runner, to fix the same defect in sibling code, and to fix rather than report remaining wrong output that is part of the task.
- Measurements, claude-sonnet-4-6, same harness:

  | Set | Before | After |
  |---|---|---|
  | 14 previously unresolved (rerun, same session) | 3/14 | 5/14 |
  | seed 1 (20) | 14/20 (v2) | 15/20 (v3) |
  | seed 2 (20) | 13/20 | 14/20 (v2) |

  The baseline rerun of the 14 shows run-to-run noise: 3 of them resolved with the unchanged prompt. Every difference above is within noise (±2 per 20), so this is weak evidence. The direction agrees across all three sets, and the change costs nothing on E2E: `scripts/e2e.sh claude-sonnet-4-6` on the branch gave `pass=11 fail=0`. Haiku was not rerun.
- PR #52: a multi-line paste in the REPL is one prompt, not one prompt per line (pty test fails without the fix).
- Next candidates: rerun haiku with the new prompt; the 5–6 instances still unresolved look like hidden-spec cases, so further SWE-bench work has diminishing returns. Long-session UX (B-COMPACT) still has no evidence of need.

## 2026-10-29 — SWE-bench seed 2; small UX and process items (PRs #45–#49)

- SWE-bench Lite sympy, **seed 2** (a second sample of 20; it overlaps the seed-1 set), main at 0.3.0+:
  - claude-sonnet-4-6: 13/20
  - claude-haiku-4-5: 13/20

  Reports are in `docs/bench/swebench-lite-sympy-seed2-*.json`.
- Combined with seed 1 (v2): sonnet 27/40, haiku 28/40, i.e. about 68–70%. The two models are statistically indistinguishable on this subset with this harness. Treat ~2/3 resolved as the current level, not a precise number.
- PR #45: README has a measured-results table and installs from the release.
- PR #46: `.claude/agents/adversarial-reviewer.md` codifies the review process.
- PR #47: `--resume` accepts unique id prefixes.
- PR #48: B-PARALLEL dropped. In a real run, tools took 0.10 s against 9.7 s of model time.

## 2026-10-29 — 0.3.0 released

- https://github.com/yezhoufan2005/CLI-Agent/releases/tag/v0.3.0 was built by the release workflow after `main` CI passed. Verified: downloaded the assets, `sha256sum -c` ok, clean-venv install reports `cli-agent 0.3.0`.
- Next candidates:
  1. Analyse the 5–6 SWE-bench instances that remain unresolved for agent-side causes.
  2. A second SWE-bench seed to reduce noise.
  3. Long-running interactive UX (session listing, a resume picker).

## 2026-10-29 — Release 0.3.0 gate evidence

| Gate | Evidence |
|---|---|
| 1. CI green | `main` at 5abdb73: all 7 jobs green (ubuntu/macos × 3.11/3.13/3.14, plus build). The release workflow re-runs the gate |
| 2. ACs | No new ACs. Behaviour changes are covered by CI tests (`test_review4.py`, fuzzy edit, continuation) |
| 3. E2E on the release commit | `scripts/e2e.sh claude-sonnet-4-6` at 5abdb73: `pass=11 fail=0` |
| 4. No open `severity:high` | Only #17 (optional PyPI) is open |
| 5. Independent review | The fourth review covered all code since 0.2.0. All 9 findings are fixed (#42) |
| 6. Wheel installs | Release workflow smoke test, then a manual install from the release asset |

## 2026-10-29 — Fourth independent review: fixes (PR #42)

A reviewer agent checked the code changed since d94e5b6 and reproduced 9 findings. Status:

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | med | The fuzzy edit split on all `str.splitlines` separators and rewrote form feeds and similar characters as `\n` | fixed: splits on `\n` only, and refuses windows containing `\r`, VT, FF, NEL, LS or PS (CI) |
| 2 | med | The stream deadline capped total time, so long but healthy streams were cut and retried | fixed: it is now an idle deadline (300 s) that content lines reset and pings don't (CI) |
| 3 | med-low | A continue prompt from a failed or interrupted run was merged into the user's next prompt | fixed: a trailing stale continue prompt is dropped before adding the new prompt (CI) |
| 4 | low | doctor exited 0 despite ✗ checks | fixed |
| 5 | low | doctor's MCP check ignored the real cwd and the executable bit | fixed: it resolves the command against `mcp-cwd` and checks `X_OK` |
| 6 | low | CR-only files got mixed line endings | fixed by #1: such windows are refused (CI) |
| 7 | low | A failed fuzzy match took 7–35 s on large files | fixed: windows are anchored on the first non-blank line using pre-stripped lines (CI: <2 s on 50k lines) |
| 8 | low | `final_text` lost the earlier parts of a continued reply | fixed: continued text is concatenated (CI) |
| 9 | low | A fuzzy edit that changes nothing reported success | fixed: raises "would not change the file" (CI) |

Not acted on (unverified): `model_context_window_exceeded` maps to `max_tokens` and may trigger continuations into a full context. That is bounded by MAX_CONTINUATIONS=3.

## 2026-10-29 — SWE-bench rerun after fixes; CI on 3.14 (PRs #36–#40)

- Full rerun of the same 20 SWE-bench Lite sympy instances (seed 1) on main after the max_tokens continuation fix (#36), the whitespace-tolerant edit fallback (#38) and the stream deadline (#39). Reports: `docs/bench/swebench-lite-sympy-seed1-*-v2.json`.
  - claude-sonnet-4-6: **14/20** (was 12/20). Unresolved: 11897, 13177, 14308, 14817, 17630, 17655.
  - claude-haiku-4-5: **15/20** (was 13/20). Unresolved: 11897, 13177, 13915, 14308, 17630.
  - No run ended with status `max_tokens` this time.
  - Caveat: n=20 and a single run per model, so ±2 resolved is within run-to-run noise. The harness itself undercounts by about 1/20 under load (see the gold-patch check above). Sonnet vs haiku is not separated.
- PR #37: `doctor` checks bash, git, the state dir, the sandbox and MCP commands. PR #39: overall 15-minute stream deadline. PR #40: CI adds Python 3.14 on both OSes, and the ruleset now requires all 7 jobs.
- Prompt caching: a raw request with an explicit `cache_control` block, sent twice through the dev proxy, returned no `cache_*` usage fields at all. So the proxy does not forward caching; our requests are well-formed per the API docs. The real API is untested from here (no direct key).

## 2026-10-28 — SWE-bench Lite (sympy subset), Docker-free (PR #35)

- `scripts/swebench_sympy.py` runs SWE-bench Lite sympy instances without Docker:
  - Setup: one git worktree per instance at `base_commit`, and a shared Python 3.9 venv with mpmath 1.3.0 (SWE-bench's sympy environment).
  - The agent sees only the problem statement.
  - Then the test files are reset, the hidden `test_patch` is applied, and sympy's `bin/test` runs.
  - Resolved means every FAIL_TO_PASS and PASS_TO_PASS test passes.
- Harness validation with the gold patches (n=20, seed 1): 19/20 resolved when run 6 in parallel. The one miss (sympy-13146) resolves when run alone; RecursionErrors in neighbouring evalf tests suggest flakiness under load. The harness may therefore undercount by about 1 in 20. These are not official SWE-bench numbers (different runner, no Docker).
- Results (same 20 instances, `--max-turns 60`; reports in `docs/bench/swebench-lite-sympy-seed1-*.json`):
  - claude-sonnet-4-6: **12/20**
  - claude-haiku-4-5: **13/20**
- Finding: 4 of haiku's 7 misses ended with status `max_tokens`. The model's reply was truncated with no tool call and the agent loop stopped. That is a product defect: the loop should ask the model to continue.
- Fix (PR #36): the loop now sends a continue prompt after a truncated reply, up to 3 times in a row. Rerun of those 4 haiku instances with the fix: all 4 ended `done` (none stopped at max_tokens), and **2/4 now resolve** (17655, 19254). With this fix the haiku subset result would be 15/20. I did not rerun the full 20 for either model, so 15/20 is an estimate from the rerun, not a fresh measurement.

## 2026-10-28 — Real third-party integrations verified (manual)

- **MCP**: `@modelcontextprotocol/server-filesystem` (via `npx -y`, rooted at a scratch dir) configured under `[mcp_servers.fs]`. cli-agent (sonnet-4-6, `--mode auto`) reported "MCP: 14 tool(s) from fs", called `fs.read_text_file`, and returned the file's exact contents. (verified, manual)
- **OpenAI-compatible provider**: Ollama 11434 `/v1` with `qwen2.5:1.5b` (installed in the container for this run only).
  - Non-streaming (`--json`): the model called `read_file`, and the run exited 0 with the correct answer.
  - Streaming (terminal UI): the streamed tool call was parsed and executed. The small model invented an absolute path, the confinement check rejected it with a clear error, and the model reported that. Exit 0. Usage parsing worked (`cache read 2309` from Ollama's `cached_tokens`).
  - The adapter is verified against a real server. The small model's answer quality is not ours.
- This removes "tested only against fakes" for both integrations. CHANGELOG's Unreleased limitation is updated.

## 2026-10-28 — 0.2.0 released; state for the next session

- https://github.com/yezhoufan2005/CLI-Agent/releases/tag/v0.2.0 was built by the release workflow from main after CI passed. Verified: downloaded the assets, `sha256sum -c` ok, clean-venv install reports `cli-agent 0.2.0`.
- State: main is green; 212 tests (5 sandbox tests run only on Linux CI); E2E 11/11; Exercism bench 27/30 for both models. The only open issue is #17 (optional PyPI, needs-human).
- Suggested next work, highest value first:
  1. ~~Real third-party integrations~~: done, see the entry above.
  2. **Bench difficulty.** Exercism doesn't separate haiku from sonnet. Try a small SWE-bench Lite subset; needs Docker-free setup, so investigate first.
  3. **Prompt caching.** `cache_read` is 0 via the dev proxy. Test against an endpoint known to support caching, or document it as proxy-specific.
  4. **UX.** Ctrl-C during a tool call in the REPL (should cancel the tool, not the session), plus `--resume` listing/picker niceties.

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
