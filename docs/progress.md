# Progress log

Newest entry first. Each entry: what changed, what was verified (with evidence), what is open, what to do next.

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
