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

Any OpenAI-compatible Chat Completions endpoint (OpenAI, OpenRouter, Ollama, vLLM, LiteLLM, …) also works:

```bash
export OPENAI_API_KEY=...                         # optional for local servers
export OPENAI_BASE_URL=http://localhost:11434/v1  # default: https://api.openai.com/v1
cli-agent --provider openai --model qwen2.5-coder "explain src/main.py"
```

The OpenAI-compatible provider does not stream yet; text appears when each turn completes.

## Use

```bash
cli-agent "the tests in tests/test_parser.py fail; fix them"   # one task, then exit
echo "add a --verbose flag" | cli-agent --mode auto-edit       # task from stdin
cli-agent                                                       # interactive session
cli-agent --continue "now update the docs"                      # continue last session here
cli-agent --json "summarize this repo" | jq .                   # JSON-lines events for scripts
cli-agent sessions                                              # list saved sessions
cli-agent undo                                                  # revert file changes of the last prompt
```

`cli-agent undo` (or `/undo` in a session) puts back the files the agent's file tools changed during the most recent prompt, and deletes files it created. Each further undo goes back one more prompt, up to 20. If you edited a file yourself after the agent wrote it, undo leaves that file alone. Changes made through shell commands are not tracked; use git for those.

Exit codes: `0` done, `1` error (model/API failure, refusal), `2` usage/config error, `3` stopped incomplete (turn or token limit), `130` interrupted. An interrupted or failed session is saved and can be resumed with `--resume ID`.

## Permissions

| Mode | Read tools | File writes | Shell commands |
|---|---|---|---|
| `read-only` | allow | deny | deny |
| `ask` (default) | allow | ask | ask |
| `auto-edit` | allow | allow | ask |
| `auto` | allow | allow | allow |

When there is no terminal to ask on (stdin piped, `--json`), "ask" means "deny".

Rules refine the mode: `--allow 'run_shell:pytest*'`, `--deny 'run_shell:git push*'`, or `allow = [...]` / `deny = [...]` in `~/.config/cli-agent/config.toml`. Deny rules win over allow rules.

- Path rules match the path normalized relative to the workspace, so `src/../.env` is matched as `.env`.
- A shell allow glob never matches a command line that contains shell metacharacters (`; & | $ ` < > ( )`, newlines). `run_shell:pytest*` allows `pytest -q tests/`, but `pytest; curl … | sh` still asks.
- Shell deny rules are also checked against each simple command in a line (`cd x && git push` matches `git push*`). They are best-effort, not a security boundary.
- "Always" for a shell command approves exactly that command line for the session.
- Writes under `.git/` (hooks, config) always ask, even in `auto` and `auto-edit`, unless an explicit path rule such as `write_file:.git/info/*` allows them.
- A small built-in guard blocks catastrophic shell commands (`rm -rf ~`, `mkfs`, force-push, …) in every mode.

Each shell command runs with empty stdin, a timeout (default 120 s, max 600 s), and a 50 MB output cap. When it finishes, anything it left running (`&` jobs and, on Linux, `setsid`/`nohup` escapees) is killed, so an agent cannot leave daemons behind.

### Shell sandbox (Linux)

`--sandbox auto` (or `sandbox = "auto"` in the config file, or `CLI_AGENT_SANDBOX=auto`) runs every shell command inside [bubblewrap](https://github.com/containers/bubblewrap):
- The filesystem is read-only except for the workspace and a private `/tmp`.
- Your home directory is replaced by an empty directory, so `~/.ssh` and `~/.aws` are not visible.
- There is no network unless you set `sandbox_network = true`.
- The command runs in its own PID namespace.

`auto` falls back to unsandboxed execution with a warning when bubblewrap cannot run. `required` refuses to start in that case. Bubblewrap needs unprivileged user namespaces, so it does not work inside most Docker containers. On Ubuntu 24.04 it may need `kernel.apparmor_restrict_unprivileged_userns=0` or an AppArmor profile. The default is `off`.

File tools cannot touch paths outside the workspace (symlinks are resolved first; list/search skip symlinks). Shell commands run with your user's privileges and are **not sandboxed**: the workspace boundary applies only to file tools, and an approved command can read or change anything your user can. Credential-like environment variables (`*TOKEN*`, `*KEY*`, `*SECRET*`, `*_PASS`, `DATABASE_URL`, `SSH_AUTH_SOCK`, …) are removed from the environment of shell commands, and on Linux the agent marks itself non-dumpable so commands cannot read its environment through `/proc`. Credential *files* your user can read (e.g. `~/.aws/credentials`) remain readable to shell commands.

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

Precedence: defaults < config file < environment (`CLI_AGENT_PROVIDER`, `CLI_AGENT_MODEL`, `CLI_AGENT_MODE`, `ANTHROPIC_*` / `OPENAI_*`) < flags. `provider = "openai"` in the config file selects the OpenAI-compatible provider.

## Development

```bash
./scripts/check.sh      # format, lint, type-check, tests — the same gate CI runs
```

Docs: [product spec](docs/product.md) · [architecture](docs/architecture.md) · [operating model](docs/operating-model.md) · [backlog](docs/backlog.md) · [progress](docs/progress.md).

License: MIT.
