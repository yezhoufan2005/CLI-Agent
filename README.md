# cli-agent

An autonomous coding agent for your terminal. Give it a task in plain language; it reads your code, edits files, runs commands, and checks its own work — inside one workspace directory, under a permission policy you control.

Status: early (0.x). See [docs/progress.md](docs/progress.md) for what is verified and what is not.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or pip).

```bash
uv tool install git+https://github.com/yezhoufan2005/CLI-Agent
# or, from a checkout:
uv tool install .
```

Set credentials in the environment (never in a config file):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or, for an Anthropic-compatible proxy:
export ANTHROPIC_BASE_URL=https://proxy.example
export ANTHROPIC_AUTH_TOKEN=...
cli-agent doctor        # checks config and makes one tiny model call
```

## Use

```bash
cli-agent "the tests in tests/test_parser.py fail; fix them"   # one task, then exit
echo "add a --verbose flag" | cli-agent --mode auto-edit       # task from stdin
cli-agent                                                       # interactive session
cli-agent --continue "now update the docs"                      # continue last session here
cli-agent --json "summarize this repo" | jq .                   # JSON-lines events for scripts
cli-agent sessions                                              # list saved sessions
```

Exit codes: `0` done, `1` error (model/API failure, refusal), `2` usage/config error, `3` stopped incomplete (turn or token limit), `130` interrupted. An interrupted or failed session is saved and can be resumed with `--resume ID`.

## Permissions

| Mode | Read tools | File writes | Shell commands |
|---|---|---|---|
| `read-only` | allow | deny | deny |
| `ask` (default) | allow | ask | ask |
| `auto-edit` | allow | allow | ask |
| `auto` | allow | allow | allow |

When there is no terminal to ask on (stdin piped, `--json`), "ask" means "deny".

Rules refine the mode: `--allow 'run_shell:pytest*'`, `--deny 'run_shell:git push*'`, or `allow = [...]` / `deny = [...]` in `~/.config/cli-agent/config.toml`. Deny rules win over allow rules. A small built-in guard blocks catastrophic shell commands (`rm -rf /`, `mkfs`, force-push, …) in every mode.

File tools cannot touch paths outside the workspace (symlinks are resolved first). Shell commands run with your user's privileges and are **not sandboxed**; the workspace boundary applies only to file tools. Credential-like environment variables (`*TOKEN*`, `*KEY*`, `*SECRET*`, …) are removed from the environment of shell commands.

`AGENTS.md` / `CLAUDE.md` in the workspace root are given to the model as project conventions; they cannot change permissions.

## Configuration

`~/.config/cli-agent/config.toml` (override with `CLI_AGENT_CONFIG` or `--config`):

```toml
model = "claude-sonnet-4-6"
mode = "auto-edit"
allow = ["run_shell:pytest*", "run_shell:git status*", "run_shell:git diff*"]
deny = ["run_shell:git push*"]
max_turns = 50
shell_timeout = 120
```

Precedence: defaults < config file < environment (`CLI_AGENT_MODEL`, `CLI_AGENT_MODE`, `ANTHROPIC_*`) < flags.

## Development

```bash
./scripts/check.sh      # format, lint, type-check, tests — the same gate CI runs
```

Docs: [product spec](docs/product.md) · [architecture](docs/architecture.md) · [operating model](docs/operating-model.md) · [backlog](docs/backlog.md) · [progress](docs/progress.md).

License: MIT.
