# CLAUDE.md

cli-agent: a Python 3.11+ terminal coding agent. Mission: `instruction.md`. Run authority/limits: `autonomy/ENVIRONMENT.md` (do not edit).

## Resume here
1. Read the latest entry in `docs/progress.md`, then `docs/backlog.md`.
2. Check open GitHub issues (esp. `needs-human`), PRs, and CI on `main` (`gh run list -L 5`).

## Commands
- `./scripts/check.sh` — the quality gate (ruff format/lint, mypy --strict, pytest). Must pass before every commit.
- `./scripts/e2e.sh [model]` — real-model tasks in `tests/e2e/*/` (needs `ANTHROPIC_*` env; local only, never in CI).
- `uv run cli-agent ...` — run from the checkout.

## Layout
- `src/cli_agent/agent.py` loop · `permissions.py` policy · `tools/` tools · `providers/` model adapters · `session.py` persistence · `cli.py` entry · `ui.py` output.
- Docs: `docs/product.md` (spec + acceptance criteria + release gates), `docs/architecture.md` (decisions), `docs/operating-model.md` (how to work).

## Invariants (enforced by tests; keep them)
- Tools never decide permissions; every tool declares `effect` and goes through `PermissionPolicy.check`.
- File tools confine paths with `resolve_in_root` (symlinks resolved).
- Nothing in the workspace can grant permissions; only user config, flags, interactive approval.
- Non-interactive runs never prompt: "ask" becomes "deny".
- Shell env is scrubbed of credential-like variables; never log or persist secrets.
- Session history must stay API-valid (every tool_use has a tool_result) — see `Session.repair`.
- CI must pass without a real model (use `FakeProvider` / `httpx.MockTransport`).

## Conventions
- Dependencies pinned exactly in `pyproject.toml`; `uv.lock` committed.
- Small PRs; `gh pr merge N --squash --auto` (ruleset requires all CI jobs, no bypass); message says why. Record progress in `docs/progress.md`.
- Repo is public: no secrets in commits, issues, logs.
