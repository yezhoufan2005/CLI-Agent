# cli-agent

An autonomous coding agent for your terminal. Give it a task in plain language; it reads your code, edits files, runs commands, and checks its own work — inside one workspace directory, under a permission policy you control.

Status: early (0.x). See [docs/progress.md](docs/progress.md) for what is verified and what is not.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or pip).

```bash
uv tool install cli-agent-yzf     # from PyPI; installs the `cli-agent` command
# or a GitHub release wheel:
uv tool install https://github.com/yezhoufan2005/CLI-Agent/releases/download/v0.7.0/cli_agent_yzf-0.7.0-py3-none-any.whl
# or the latest main:
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

`cli-agent undo` (or `/undo` in a session) puts back the files the agent's file tools changed during the most recent prompt, and deletes files it created. Each further undo goes back one more prompt, up to 20. If you edited a file yourself after the agent wrote it, undo leaves that file alone. Changes made through shell commands are not tracked; undo tells you how many shell commands ran in that prompt, so check `git status` for those. The agent is instructed to change file contents only through its file tools, so most edits are covered.

Exit codes: `0` done, `1` error (model/API failure, refusal), `2` usage/config error, `3` stopped incomplete (turn limit, output-token limit, or `--max-session-tokens` budget), `130` interrupted (Ctrl-C), `129`/`143` terminated by SIGHUP/SIGTERM (the running command is killed and the session saved first). An interrupted or failed session is saved and can be resumed with `--resume ID`.

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
- A shell allow glob never matches a command line that contains shell metacharacters (`; & | $ ` < > ( )`, newlines). `run_shell:pytest*` allows `pytest -q tests/`, but `pytest; curl … | sh` still asks. The same applies to harmless pipes such as `pytest -q | tail`; in a headless run the agent is told which rule nearly matched, so it can retry the plain command.
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
model = "claude-sonnet-5-5"
mode = "auto-edit"
allow = ["run_shell:pytest*", "run_shell:git status*", "run_shell:git diff*"]
deny = ["run_shell:git push*"]
max_turns = 50
max_session_tokens = 2000000   # optional cost cap per session (input + output tokens)
shell_timeout = 120
```

### MCP servers

Tools from [MCP](https://modelcontextprotocol.io) stdio servers can be added in the user config file only; a repository cannot add servers:

```toml
[mcp_servers.fs]
command = ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
env = { SOME_SETTING = "value" }     # optional; passed to the server as-is
```

They appear to the model as `mcp__<server>__<tool>`. Every MCP tool counts as a command: it asks in `ask` and `auto-edit` mode and is denied in `read-only` mode. You can allow one with a rule such as `--allow mcp__fs__read_file`. Servers get the same scrubbed environment as shell commands, plus their configured `env`. They are not sandboxed. Only the tools part of MCP is supported; resources, prompts and sampling are not.

Precedence: defaults < config file < environment (`CLI_AGENT_PROVIDER`, `CLI_AGENT_MODEL`, `CLI_AGENT_MODE`, `ANTHROPIC_*` / `OPENAI_*`) < flags. `provider = "openai"` in the config file selects the OpenAI-compatible provider.

## How well does it work?

Measured with real models; full method and caveats in [docs/progress.md](docs/progress.md):

| Benchmark | claude-sonnet-4-6 | claude-haiku-4-5 |
|---|---|---|
| Built-in E2E tasks (`scripts/e2e.sh`, 11 tasks) | 11/11 | 11/11 |
| Exercism Python, 30 exercises, hidden tests (`scripts/bench.py`) | 27/30 | 27/30 |
| SWE-bench Lite sympy, 2 samples of 20, Docker-free (`scripts/swebench_sympy.py`) | 29/40 | 30/40 |

These are single runs on small samples, not official leaderboard numbers. The columns name the model that was requested. The development proxy used for these runs answered `claude-sonnet-4-6` requests with `claude-sonnet-5.5` and `claude-haiku-4-5` requests with `claude-haiku-5.5` (as reported by `cli-agent doctor`), so the numbers reflect those newer models.

## Development

```bash
./scripts/setup-dev.sh  # once: sync deps, enable the pre-commit hook
./scripts/check.sh      # format, lint, type-check, tests — the same gate CI runs
./scripts/e2e.sh        # real-model tasks (needs credentials; not run in CI)
```

Release procedure: [docs/release.md](docs/release.md).

Docs: [product spec](docs/product.md) · [architecture](docs/architecture.md) · [operating model](docs/operating-model.md) · [backlog](docs/backlog.md) · [progress](docs/progress.md).

License: MIT.
