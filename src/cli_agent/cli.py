"""Command-line entry point.

cli-agent [options] [PROMPT]     run one task (PROMPT or stdin), or start a REPL
cli-agent sessions               list saved sessions
cli-agent doctor                 check configuration and connectivity
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import shutil
import sys
from pathlib import Path

from cli_agent import __version__, log
from cli_agent.agent import Agent, RunResult
from cli_agent.checkpoint import Checkpoints
from cli_agent.config import Config, ConfigError, load_config
from cli_agent.mcp import MCPServer, start_servers
from cli_agent.permissions import MODES, PermissionPolicy
from cli_agent.prompt import build_system_prompt
from cli_agent.providers.base import Provider, ProviderError
from cli_agent.sandbox import SANDBOX_MODES, Sandbox, SandboxUnavailable
from cli_agent.sandbox import probe as sandbox_probe
from cli_agent.sandbox import resolve as resolve_sandbox
from cli_agent.session import Session, SessionStore, default_state_dir, new_session_id
from cli_agent.tools import ToolContext, default_tools
from cli_agent.tools.shell import scrubbed_env
from cli_agent.ui import JsonUI, TerminalUI, escape_controls
from cli_agent.workspace import git_warning

SUBCOMMANDS = ("sessions", "doctor", "undo")
DEFAULT_SESSIONS_LIMIT = 50

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INCOMPLETE = 3
EXIT_INTERRUPTED = 130

_STATUS_EXIT = {
    "done": EXIT_OK,
    "error": EXIT_ERROR,
    "refusal": EXIT_ERROR,
    "max_turns": EXIT_INCOMPLETE,
    "max_tokens": EXIT_INCOMPLETE,
    "budget": EXIT_INCOMPLETE,
    "interrupted": EXIT_INTERRUPTED,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli-agent",
        description="An autonomous coding agent for your terminal.",
        epilog="Subcommands: 'cli-agent sessions' lists sessions, 'cli-agent doctor' checks setup, "
        "'cli-agent undo' reverts the file changes of the last prompt.",
    )
    p.add_argument(
        "prompt",
        nargs="*",
        help="task to perform; omit for an interactive session (or pipe the task on stdin)",
    )
    p.add_argument("--mode", choices=MODES, help="permission mode (default from config: ask)")
    p.add_argument("--model", help="model name")
    p.add_argument(
        "--provider",
        choices=("anthropic", "openai"),
        help="model API: anthropic (default) or openai (any OpenAI-compatible endpoint)",
    )
    p.add_argument("-C", "--cwd", type=Path, help="workspace root (default: current directory)")
    which = p.add_mutually_exclusive_group()
    which.add_argument(
        "--resume",
        metavar="ID",
        help="resume a saved session (a unique prefix of the id is enough)",
    )
    which.add_argument(
        "-c",
        "--continue",
        dest="cont",
        action="store_true",
        help="continue the most recent session for this workspace",
    )
    p.add_argument(
        "--sandbox",
        choices=SANDBOX_MODES,
        help="run shell commands in a bubblewrap sandbox: off (default), auto "
        "(if available), required (refuse shell commands otherwise)",
    )
    p.add_argument("--json", action="store_true", help="emit JSON-lines events on stdout")
    p.add_argument("--max-turns", type=int, help="maximum model turns per prompt")
    p.add_argument(
        "--max-session-tokens",
        type=int,
        metavar="N",
        help="stop when the session has used N input+output tokens (cost cap)",
    )
    p.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="RULE",
        help="allow rule, e.g. 'run_shell:pytest*' (repeatable)",
    )
    p.add_argument(
        "--deny",
        action="append",
        default=[],
        metavar="RULE",
        help="deny rule, e.g. 'run_shell:git push*' (repeatable)",
    )
    p.add_argument("--config", type=Path, help="config file path")
    p.add_argument(
        "--debug-log",
        type=Path,
        metavar="FILE",
        help="append a JSON-lines debug log (timings, tokens, decisions; no content)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SESSIONS_LIMIT,
        metavar="N",
        help="with 'sessions': show at most N sessions, 0 = all (default: %(default)s)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="show tool output")
    p.add_argument("--version", action="version", version=f"cli-agent {__version__}")
    return p


def make_provider(cfg: Config) -> Provider:
    if cfg.provider == "fake":
        from cli_agent.providers.fake import FakeProvider

        script = os.environ.get("CLI_AGENT_FAKE_SCRIPT")
        if not script:
            raise ConfigError("provider 'fake' needs CLI_AGENT_FAKE_SCRIPT=<path to JSON script>")
        return FakeProvider.from_file(Path(script))
    if cfg.provider == "openai":
        from cli_agent.providers.openai import DEFAULT_BASE_URL, OpenAIProvider

        return OpenAIProvider(
            model=cfg.model, base_url=cfg.base_url or DEFAULT_BASE_URL, api_key=cfg.api_key
        )
    from cli_agent.providers.anthropic import AnthropicProvider

    return AnthropicProvider(
        model=cfg.model,
        base_url=cfg.base_url or "https://api.anthropic.com",
        api_key=cfg.api_key,
        auth_token=cfg.auth_token,
    )


def harden_process() -> bool:
    """Stop child processes from reading our credentials out of /proc.

    Shell commands run as the same user, so without this a command could read
    /proc/$PPID/environ (which still holds ANTHROPIC_API_KEY even after
    os.environ is scrubbed) or /proc/$PPID/mem. PR_SET_DUMPABLE=0 makes those
    files owned by root; children get a normal dumpable flag again on exec.
    Linux only; returns whether it took effect.
    """
    if not sys.platform.startswith("linux"):
        return False
    import ctypes

    pr_set_dumpable = 4
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return int(libc.prctl(pr_set_dumpable, 0, 0, 0, 0)) == 0
    except (OSError, AttributeError):
        return False


def drop_credentials_from_environ() -> None:
    """Remove credential-like variables from our own environment once config has
    captured them. Every child process (shell, MCP servers, multiprocessing
    helpers such as the resource tracker) then starts without them, even ones
    not launched through scrubbed_env."""
    for name in list(os.environ):
        if name not in scrubbed_env({name: ""}):
            del os.environ[name]


def _err(msg: str) -> None:
    print(f"cli-agent: {msg}", file=sys.stderr)


def checkpoints_for(root: Path) -> Checkpoints:
    key = hashlib.sha256(os.fsencode(root)).hexdigest()[:16]
    return Checkpoints(default_state_dir() / "checkpoints" / f"{key}.json", root)


def cmd_undo(cps: Checkpoints) -> int:
    try:
        report = cps.undo()
    except OSError as err:
        _err(f"undo failed: {err}")
        return EXIT_ERROR
    if report is None:
        print("nothing to undo: no file changes by the agent are recorded for this workspace")
        return EXIT_OK
    if report.shell_commands and not report.files_changed:
        print(
            f"the last prompt ({report.label}) changed no files through the file tools; "
            f"it ran {report.shell_commands} shell command(s), whose changes undo cannot "
            "revert. Run undo again to go back one more prompt."
        )
        return EXIT_OK
    print(f"undid changes from: {report.label}")
    for rel in report.restored:
        print(f"  restored {rel}")
    for rel in report.deleted:
        print(f"  deleted {rel} (the agent created it)")
    for rel in report.skipped:
        print(f"  skipped {rel} (changed since the agent wrote it; left as is)")
    if report.shell_commands:
        print(
            f"note: {report.shell_commands} shell command(s) also ran during that prompt; "
            "files they changed are not reverted (check `git status` if this is a git repository)"
        )
    return EXIT_OK


def cmd_sessions(store: SessionStore, limit: int = DEFAULT_SESSIONS_LIMIT) -> int:
    sessions = store.list()
    if not sessions:
        print("no saved sessions")
        return EXIT_OK
    for s in sessions if limit == 0 else sessions[:limit]:
        print(f"{s.id}  {s.updated[:19]}  {s.cwd}  {s.title}")
    return EXIT_OK


def environment_checks(cfg: Config) -> list[str]:
    """Local prerequisites, one line each; "✗" lines explain what to do."""
    lines = []
    for tool, why in (("bash", "run_shell needs it"), ("git", "used for change warnings")):
        found = shutil.which(tool)
        lines.append(f"✓ {tool}: {found}" if found else f"✗ {tool} not found on PATH ({why})")
    state = default_state_dir()
    try:
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        probe = state / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        lines.append(f"✓ state directory writable: {state}")
    except OSError as err:
        lines.append(f"✗ state directory {state} not writable ({err}); sessions cannot be saved")
    reason = sandbox_probe()
    if reason is None:
        lines.append(f"✓ shell sandbox available (configured: {cfg.sandbox})")
    else:
        mark = "✗" if cfg.sandbox == "required" else "-"
        lines.append(f"{mark} shell sandbox unavailable: {reason} (configured: {cfg.sandbox})")
    for server in cfg.mcp_servers:
        exe = server.command[0]
        # Resolve the way the server will be started: from the neutral MCP cwd.
        if "/" in exe:
            target = Path(exe) if Path(exe).is_absolute() else state / "mcp-cwd" / exe
            ok = target.is_file() and os.access(target, os.X_OK)
        else:
            ok = shutil.which(exe) is not None
        lines.append(
            f"✓ MCP server {server.name}: {exe}"
            if ok
            else f"✗ MCP server {server.name}: command {exe!r} not found or not executable"
        )
    return lines


def cmd_doctor(cfg: Config) -> int:
    ok = True
    print(f"cli-agent {__version__}, Python {sys.version.split()[0]}")
    print("config: " + json.dumps(cfg.redacted()))
    for line in environment_checks(cfg):
        print(line)
        if line.startswith("✗"):
            ok = False
    if cfg.provider == "anthropic" and not (cfg.api_key or cfg.auth_token):
        print("✗ no credentials: set ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN for a proxy)")
        return EXIT_ERROR
    try:
        provider = make_provider(cfg)
        from cli_agent.messages import Message, TextBlock

        result = provider.complete(
            system="Reply with the single word OK.",
            messages=[Message("user", [TextBlock("ping")])],
            tools=[],
            max_tokens=16,
        )
        print(f"✓ model {result.model or cfg.model} replied: {result.message.text()[:40]!r}")
    except (ProviderError, ConfigError) as err:
        print(f"✗ model call failed: {err}")
        ok = False
    return EXIT_OK if ok else EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    # A prompt that is exactly one subcommand word runs that subcommand.
    sub = args.prompt[0] if len(args.prompt) == 1 and args.prompt[0] in SUBCOMMANDS else ""
    try:
        cfg = load_config(args.config, provider=args.provider)
    except ConfigError as err:
        _err(str(err))
        return EXIT_USAGE
    harden_process()
    drop_credentials_from_environ()
    if args.debug_log:
        try:
            log.enable(args.debug_log)
        except OSError as err:
            _err(f"cannot open debug log {args.debug_log}: {err}")
            return EXIT_USAGE
    if args.mode:
        cfg.mode = args.mode
    if args.sandbox:
        cfg.sandbox = args.sandbox
    if args.model:
        cfg.model = args.model
    if args.max_turns is not None:
        if args.max_turns < 1:
            _err("--max-turns must be positive")
            return EXIT_USAGE
        cfg.max_turns = args.max_turns
    if args.max_session_tokens is not None:
        if args.max_session_tokens < 1:
            _err("--max-session-tokens must be positive")
            return EXIT_USAGE
        cfg.max_session_tokens = args.max_session_tokens
    store = SessionStore()
    if sub == "sessions":
        if args.limit < 0:
            _err("--limit must be 0 or positive")
            return EXIT_USAGE
        return cmd_sessions(store, args.limit)
    if sub == "doctor":
        return cmd_doctor(cfg)

    root = (args.cwd or Path.cwd()).resolve()
    if not root.is_dir():
        _err(f"workspace {root} is not a directory")
        return EXIT_USAGE
    if sub == "undo":
        return cmd_undo(checkpoints_for(root))

    prompt = " ".join(args.prompt).strip()
    stdin_tty = sys.stdin.isatty()
    if not prompt and not stdin_tty:
        prompt = sys.stdin.read().strip()
        if not prompt:
            _err("empty prompt on stdin")
            return EXIT_USAGE
    interactive_repl = not prompt
    # Approval prompts need a terminal; when stdin carried the prompt, there is none.
    can_ask = stdin_tty and not args.json

    try:
        session = _open_session(store, args, root, cfg)
    except (FileNotFoundError, ValueError) as err:
        _err(str(err))
        return EXIT_USAGE
    try:
        provider = make_provider(cfg)
    except (ConfigError, ProviderError) as err:
        _err(str(err))
        return EXIT_USAGE

    ui = JsonUI() if args.json else TerminalUI(interactive=can_ask, verbose=args.verbose)
    policy = PermissionPolicy(
        root=root, mode=cfg.mode, allow=cfg.allow + args.allow, deny=cfg.deny + args.deny
    )
    try:
        sandbox, sandbox_note = resolve_sandbox(
            cfg.sandbox,  # type: ignore[arg-type]
            root,
            cfg.sandbox_network,
        )
    except SandboxUnavailable as err:
        _err(f"sandbox=required but {err}")
        return EXIT_USAGE
    if sandbox_note:
        ui.on_notice(sandbox_note)
    if cfg.mode != "read-only":
        warning = git_warning(root)
        if warning:
            ui.on_notice(warning)
    servers: list[MCPServer] = []
    try:
        return _run(
            cfg,
            root,
            ui,
            policy,
            provider,
            sandbox,
            store,
            session,
            prompt,
            interactive_repl,
            servers,
        )
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return EXIT_INTERRUPTED
    finally:
        for server in servers:
            server.close()


def _run(
    cfg: Config,
    root: Path,
    ui: TerminalUI | JsonUI,
    policy: PermissionPolicy,
    provider: Provider,
    sandbox: Sandbox | None,
    store: SessionStore,
    session: Session,
    prompt: str,
    interactive_repl: bool,
    servers: list[MCPServer],
) -> int:
    builtin = default_tools()
    # Servers start in a neutral directory: launchers load code from their cwd.
    mcp_cwd = default_state_dir() / "mcp-cwd"
    if cfg.mcp_servers:
        mcp_cwd.mkdir(parents=True, exist_ok=True, mode=0o700)
    started, mcp_tools, problems = start_servers(
        cfg.mcp_servers, str(mcp_cwd), reserved={t.name for t in builtin}
    )
    servers.extend(started)
    for problem in problems:
        ui.on_notice(problem)
    if mcp_tools:
        names = ", ".join(sorted({t.server.cfg.name for t in mcp_tools}))
        ui.on_notice(f"MCP: {len(mcp_tools)} tool(s) from {names}")
    agent = Agent(
        provider=provider,
        tools=[*builtin, *mcp_tools],
        policy=policy,
        ctx=ToolContext(
            root=root,
            shell_timeout=cfg.shell_timeout,
            sandbox=sandbox,
            checkpoints=checkpoints_for(root),
        ),
        ui=ui,
        system=build_system_prompt(root),
        max_turns=cfg.max_turns,
        max_session_tokens=cfg.max_session_tokens,
        context_chars=cfg.context_chars,
        max_tokens=cfg.max_tokens,
        save=store.save,
    )
    if interactive_repl:
        return _repl(agent, session, ui, store)
    result = agent.run(session, prompt)
    return _finish(result, session, ui)


def _open_session(
    store: SessionStore, args: argparse.Namespace, root: Path, cfg: Config
) -> Session:
    if args.resume is not None:
        session = store.load(store.resolve_id(args.resume))
    elif args.cont:
        found = store.latest_for(str(root))
        if found is None:
            raise FileNotFoundError(f"no previous session for {root}")
        session = found
    else:
        return Session(id=new_session_id(), cwd=str(root), model=cfg.model)
    if session.cwd != str(root):
        raise ValueError(f"session {session.id} belongs to {session.cwd}; run it there or use -C")
    return session


def _finish(result: RunResult, session: Session, ui: TerminalUI | JsonUI) -> int:
    if isinstance(ui, JsonUI):
        ui.emit(
            "result",
            status=result.status,
            turns=result.turns,
            error=result.error,
            text=result.final_text,
            session_id=session.id,
            usage=session.usage.to_dict(),
        )
    else:
        u = session.usage
        print(
            f"[{result.turns} turn(s); tokens in {u.input_tokens}, out {u.output_tokens}, "
            f"cache read {u.cache_read_tokens}]",
            file=sys.stderr,
        )
    if not isinstance(ui, JsonUI) and result.status != "done":
        _err(f"{result.status}: {result.error}")
        print(
            f"session {session.id} saved; resume with: cli-agent --resume {session.id}",
            file=sys.stderr,
        )
    return _STATUS_EXIT[result.status]


PASTE_WAIT = 0.05


def _input_pending() -> bool:
    """True if more input is already waiting on stdin, as it is mid-paste."""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], PASTE_WAIT)
    except (OSError, ValueError):
        return False
    return bool(ready)


def _read_prompt(queued: list[str]) -> tuple[str, bool]:
    """Read one prompt; return it and whether stdin hit EOF right after it.

    `queued` holds lines typed ahead during an approval question; they come first.
    """
    if queued:
        first = queued.pop(0)
        print(f"› {first}", file=sys.stderr)
        if first.lstrip().startswith("/"):
            return first.strip(), False
        lines = [first]
        while queued and not queued[0].lstrip().startswith("/"):
            lines.append(queued.pop(0))
        return "\n".join(lines).strip(), False
    first = input("› ")
    # Lines typed ahead while the agent was busy are waiting too; a command must
    # stay on its own so that e.g. `/mode read-only` still applies.
    if first.lstrip().startswith("/"):
        return first.strip(), False
    # A multi-line paste arrives all at once. Join the lines that are already
    # waiting, so it becomes one prompt instead of one model run per line.
    lines = [first]
    while _input_pending():
        try:
            lines.append(input(""))
        except EOFError:
            return "\n".join(lines).strip(), True
    return "\n".join(lines).strip(), False


def _list_workspace_sessions(store: SessionStore, current: Session, limit: int = 10) -> None:
    mine = [s for s in store.list() if s.cwd == current.cwd]
    if not any(s.id == current.id for s in mine):
        mine.insert(0, current)  # not saved yet: nothing has been sent
    for s in mine[:limit]:
        mark = "*" if s.id == current.id else " "
        title = escape_controls(s.title, keep_newlines=False) or "(no prompt yet)"
        print(f"{mark} {s.id}  {s.updated[:19]}  {title}", file=sys.stderr)
    if len(mine) > limit:
        print(f"  … {len(mine) - limit} more (cli-agent sessions --limit 0)", file=sys.stderr)


def _repl(
    agent: Agent, session: Session, ui: TerminalUI | JsonUI, store: SessionStore | None = None
) -> int:
    try:
        import readline  # noqa: F401  - enables line editing for input()
    except ImportError:
        pass
    print(
        f"cli-agent {__version__} — session {session.id} — mode {agent.policy.mode}. "
        "Type /exit to quit, /help for commands.",
        file=sys.stderr,
    )
    queued: list[str] = []
    if isinstance(ui, TerminalUI):
        ui.keep_typeahead = True
        queued = ui.typeahead
    eof = False
    while True:
        if eof:
            print(file=sys.stderr)
            return EXIT_OK
        try:
            line, eof = _read_prompt(queued)
        except EOFError:
            print(file=sys.stderr)
            return EXIT_OK
        except KeyboardInterrupt:
            print(file=sys.stderr)
            continue
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return EXIT_OK
        if line == "/help":
            print(
                "/exit  quit\n"
                "/undo  revert the file changes of the last prompt\n"
                "/usage  token usage\n"
                "/mode MODE  change permission mode\n"
                "/sessions  list this workspace's sessions\n"
                "/resume ID  switch to another session of this workspace (id prefix is enough)",
                file=sys.stderr,
            )
            continue
        if line == "/undo":
            if agent.ctx.checkpoints is not None:
                cmd_undo(agent.ctx.checkpoints)
            continue
        if line == "/sessions" and store is not None:
            _list_workspace_sessions(store, session)
            continue
        if (line == "/resume" or line.startswith("/resume ")) and store is not None:
            parts = line.split()
            if len(parts) != 2:
                print("usage: /resume ID  (see /sessions)", file=sys.stderr)
                continue
            try:
                other = store.load(store.resolve_id(parts[1]))
            except (FileNotFoundError, ValueError) as err:
                print(str(err), file=sys.stderr)
                continue
            if other.cwd != session.cwd:
                print(
                    f"session {other.id} belongs to {other.cwd}, not this workspace",
                    file=sys.stderr,
                )
                continue
            session = other
            print(f"now in session {session.id}: {session.title}", file=sys.stderr)
            continue
        if line == "/usage":
            print(json.dumps(session.usage.to_dict()), file=sys.stderr)
            continue
        if line == "/mode" or line.startswith("/mode "):
            parts = line.split()
            if len(parts) == 2 and parts[1] in MODES:
                agent.policy.mode = parts[1]  # type: ignore[assignment]
                print(f"mode is now {parts[1]}", file=sys.stderr)
            else:
                print(f"usage: /mode {{{','.join(MODES)}}}", file=sys.stderr)
            continue
        if line.startswith("/") and " " not in line:
            # Likely a mistyped command; don't spend a model turn on it.
            print(f"unknown command {line}; /help lists commands", file=sys.stderr)
            continue
        result = agent.run(session, line)
        if result.status != "done":
            _err(f"{result.status}: {result.error}")


if __name__ == "__main__":
    sys.exit(main())
