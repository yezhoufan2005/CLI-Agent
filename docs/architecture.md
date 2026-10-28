# Architecture

## Overview

```
cli.py ──► config.py (defaults < user file < env < flags)
   │
   ├─► prompt.py   system prompt + AGENTS.md/CLAUDE.md (as data, fenced)
   ├─► session.py  JSON session store, atomic save, repair on resume
   └─► agent.py    loop: provider.complete ─► permissions.check ─► (ui.approve) ─► tool.run
                      │                         │
                      ▼                         ▼
               providers/               tools/  read_file list_files search_files
               anthropic.py (httpx)             write_file edit_file run_shell
               fake.py (scripted)
                      ui.py  TerminalUI (stdout text, stderr activity) | JsonUI (JSONL)
```

Pure Python 3.11+, one runtime dependency (`httpx`). Everything is synchronous; a turn is one blocking HTTP call followed by sequential tool execution.

## Key decisions

ADR-style; status is "accepted" unless noted. Revisit when evidence contradicts them.

### D1 Python + httpx, no vendor SDK

Python is the most common language for agent tooling and is preinstalled on developer machines; `uv tool install` gives a one-command install. Calling the Messages API with httpx directly (instead of the `anthropic` SDK) keeps the dependency tree to one package, makes the wire format explicit and contract-testable with `httpx.MockTransport`, and works unchanged with Anthropic-compatible proxies. Cost: we own retry and error parsing (~100 lines). Alternatives: TypeScript/Node (larger runtime footprint for users, same API work), Go/Rust (single binary but slower iteration).

### D2 Canonical message model = Anthropic content blocks

`messages.py` stores text / tool_use / tool_result blocks. Other providers will translate to and from it. The model is plain dataclasses and JSON-serializable, which is also the session format.

### D3 Small fixed tool set with structured file tools

Six tools. Evidence (see `docs/research.md`) suggests a minimal tool set is competitive and that exact-string replacement is a reliable edit format for current models. Structured file tools exist (rather than bash-only) because they let us enforce workspace confinement and the read-before-edit rule, and give good error messages ("occurs 3 times; add context").

### D4 Permission policy separate from tools

Every tool declares an `effect` (`read`/`write`/`execute`). `PermissionPolicy.check` decides allow/deny/ask from: built-in shell guard → deny rules → read-only floor → allow rules → mode default. Tools never decide their own permission. Non-interactive runs turn "ask" into "deny" so headless runs can never hang or silently escalate.

Trust boundary: the model, repository files, and tool output are untrusted. Only the user's config file, CLI flags, and interactive answers can grant permissions. Project instruction files are fenced in the system prompt as conventions that cannot override safety rules (a mitigation, not a guarantee — prompt injection is not solved; the policy layer is what actually enforces limits).

### D5 Shell is not sandboxed in 0.1 (accepted limitation)

`run_shell` runs `bash -c` in the workspace with a scrubbed environment, empty stdin, own process group, timeout, and bounded output. It is not an OS sandbox: an approved command can touch anything the user can. bubblewrap/Landlock sandboxing is backlog item B-SANDBOX; the container used for development lacks unprivileged user namespaces, so it cannot be tested here yet.

### D6 Sessions are whole-file JSON, rewritten atomically each turn

Simple and robust (temp file + fsync + rename, mode 0600). Write cost is O(history) per turn, acceptable for sessions of a few MB. `Session.repair()` appends error tool_results for any tool_use left without a result, so an interrupted history is always valid to send.

### D7 Context management by eliding old tool output

When the estimated history size exceeds a character budget, the oldest tool results (outside the last 6 messages) are replaced with a marker. Cheap, deterministic, and preserves the user's and model's own words. Summarization-based compaction is deferred until long-session E2E shows it is needed.

### D8 Prompt caching

The adapter marks the system prompt and the final message block with `cache_control: ephemeral`, so each turn reuses the cached prefix. The marker is added to a copy of the wire message; the stored history is unchanged.

## Error handling

- Provider errors carry `retryable` and `retry_after`; `with_retries` does exponential backoff with jitter (max 6 attempts, ≤30 s). Non-retryable errors end the run with exit code 1 and the session saved.
- Tool errors (`ToolError`) and unexpected tool exceptions become `is_error` tool results so the model can recover.
- Ctrl-C during a run: the in-flight shell process group is killed, the session is repaired and saved, exit 130.

## Testing strategy

- Unit tests per module; contract tests for the Anthropic wire format with `httpx.MockTransport`.
- Agent loop tests with `FakeProvider` (scripted turns, records every request).
- CLI tests run `python -m cli_agent` as a subprocess with the fake provider (`provider = "fake"` + `CLI_AGENT_FAKE_SCRIPT`), covering exit codes and headless behaviour.
- E2E against a real model: `scripts/e2e.sh` (local only).

### D9 MCP tools are "execute" and user-configured only

MCP servers come only from the user config file, never from the workspace. That avoids the "repo config runs code" class of problems seen in other agents (see `docs/research.md`). An MCP tool's effect cannot be known, so all of them are treated as `execute`. The client is synchronous: newline-delimited JSON-RPC over stdio, with a reader thread and per-request timeouts. A server that fails to start is reported and skipped, not fatal. Only `tools/list` and `tools/call` are implemented.
