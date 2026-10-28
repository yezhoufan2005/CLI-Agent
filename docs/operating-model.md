# Autonomous operating model

How work on this repository is selected, executed, verified, and recorded. Applies to human and agent contributors.

## State lives in the repository

- `docs/backlog.md` — prioritized task list with status. The source of truth for "what next".
- `docs/progress.md` — dated log of sessions: what changed, what was verified (with evidence), what is open. Read the latest entry first when resuming.
- GitHub issues — defects and anything needing discussion; `needs-human` for genuine external blockers only (see `instruction.md` §9).
- Git history — every change, with the reason in the message.

## Loop (one task at a time)

1. Observe: `git log`, latest `docs/progress.md` entry, open issues/PRs, CI status on `main`.
2. Pick the highest-priority `todo` item in `docs/backlog.md` whose dependencies are met (P0 before P1; defects in shipped behaviour before new features).
3. Write down the acceptance check before coding (a failing test where possible).
4. Implement the smallest coherent change.
5. Verify: `./scripts/check.sh` must pass. For behaviour that depends on a real model, also run `./scripts/e2e.sh` locally and record the result line.
6. Challenge: for changes to permissions, path confinement, shell execution, config trust, or session persistence, get an independent review (a fresh reviewer agent given the diff and the relevant spec, not the author's rationale). Fix or record every finding.
7. Integrate: branch → PR → `gh pr merge N --squash --auto`. The `main` ruleset requires every CI job to pass and has no bypass, so nothing (including doc-only changes) lands on `main` without green CI. Never merge with a red or pending check.
8. Record: update backlog status and append to `docs/progress.md`.

## Verification vocabulary

Use these words precisely in progress notes and PRs:
- **verified (CI)** — covered by an automated test that ran green.
- **verified (E2E)** — observed in a real-model run; cite the `scripts/e2e.sh` result line.
- **verified (manual)** — observed by running the program; describe what was run.
- **implemented, unverified** — code exists, no evidence yet.
- **known limitation** — deliberately not handled; documented for users.

## Stopping and recovery

- Same approach failed twice → stop, write down the root-cause hypothesis, change approach.
- Flaky network: retry with backoff; commit locally and push later. Not a blocker.
- Before a session may be killed (deadline), commit and push; progress.md must say exactly where work stopped and what to do next.
- Never leave `main` red. If CI on `main` fails, fixing it is the top priority.
