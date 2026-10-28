"""Command-line entry point.

cli-agent [options] [PROMPT]     run one task (PROMPT or stdin), or start a REPL
cli-agent sessions               list saved sessions
cli-agent doctor                 check configuration and connectivity
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cli_agent import __version__
from cli_agent.agent import Agent, RunResult
from cli_agent.config import Config, ConfigError, load_config
from cli_agent.permissions import MODES, PermissionPolicy
from cli_agent.prompt import build_system_prompt
from cli_agent.providers.base import Provider, ProviderError
from cli_agent.session import Session, SessionStore, new_session_id
from cli_agent.tools import ToolContext, default_tools
from cli_agent.ui import JsonUI, TerminalUI

SUBCOMMANDS = ("sessions", "doctor")

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
    "interrupted": EXIT_INTERRUPTED,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli-agent",
        description="An autonomous coding agent for your terminal.",
        epilog="Subcommands: 'cli-agent sessions' lists sessions, 'cli-agent doctor' checks setup.",
    )
    p.add_argument(
        "prompt",
        nargs="*",
        help="task to perform; omit for an interactive session (or pipe the task on stdin)",
    )
    p.add_argument("--mode", choices=MODES, help="permission mode (default from config: ask)")
    p.add_argument("--model", help="model name")
    p.add_argument("-C", "--cwd", type=Path, help="workspace root (default: current directory)")
    p.add_argument("--resume", metavar="ID", help="resume a saved session")
    p.add_argument(
        "-c",
        "--continue",
        dest="cont",
        action="store_true",
        help="continue the most recent session for this workspace",
    )
    p.add_argument("--json", action="store_true", help="emit JSON-lines events on stdout")
    p.add_argument("--max-turns", type=int, help="maximum model turns per prompt")
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
    from cli_agent.providers.anthropic import AnthropicProvider

    return AnthropicProvider(
        model=cfg.model, base_url=cfg.base_url, api_key=cfg.api_key, auth_token=cfg.auth_token
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


def _err(msg: str) -> None:
    print(f"cli-agent: {msg}", file=sys.stderr)


def cmd_sessions(store: SessionStore) -> int:
    sessions = store.list()
    if not sessions:
        print("no saved sessions")
        return EXIT_OK
    for s in sessions[:50]:
        print(f"{s.id}  {s.updated[:19]}  {s.cwd}  {s.title}")
    return EXIT_OK


def cmd_doctor(cfg: Config) -> int:
    ok = True
    print(f"cli-agent {__version__}, Python {sys.version.split()[0]}")
    print("config: " + json.dumps(cfg.redacted()))
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
        cfg = load_config(args.config)
    except ConfigError as err:
        _err(str(err))
        return EXIT_USAGE
    harden_process()
    if args.mode:
        cfg.mode = args.mode
    if args.model:
        cfg.model = args.model
    if args.max_turns is not None:
        if args.max_turns < 1:
            _err("--max-turns must be positive")
            return EXIT_USAGE
        cfg.max_turns = args.max_turns
    store = SessionStore()
    if sub == "sessions":
        return cmd_sessions(store)
    if sub == "doctor":
        return cmd_doctor(cfg)

    root = (args.cwd or Path.cwd()).resolve()
    if not root.is_dir():
        _err(f"workspace {root} is not a directory")
        return EXIT_USAGE

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
    agent = Agent(
        provider=provider,
        tools=default_tools(),
        policy=policy,
        ctx=ToolContext(root=root, shell_timeout=cfg.shell_timeout),
        ui=ui,
        system=build_system_prompt(root),
        max_turns=cfg.max_turns,
        max_tokens=cfg.max_tokens,
        save=store.save,
    )
    if interactive_repl:
        return _repl(agent, session, ui)
    result = agent.run(session, prompt)
    return _finish(result, session, ui)


def _open_session(
    store: SessionStore, args: argparse.Namespace, root: Path, cfg: Config
) -> Session:
    if args.resume:
        session = store.load(args.resume)
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
    elif result.status != "done":
        _err(f"{result.status}: {result.error}")
        print(
            f"session {session.id} saved; resume with: cli-agent --resume {session.id}",
            file=sys.stderr,
        )
    return _STATUS_EXIT[result.status]


def _repl(agent: Agent, session: Session, ui: TerminalUI | JsonUI) -> int:
    try:
        import readline  # noqa: F401  - enables line editing for input()
    except ImportError:
        pass
    print(
        f"cli-agent {__version__} — session {session.id} — mode {agent.policy.mode}. "
        "Type /exit to quit, /help for commands.",
        file=sys.stderr,
    )
    while True:
        try:
            line = input("› ").strip()
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
                "/exit  quit\n/usage  token usage\n/mode MODE  change permission mode",
                file=sys.stderr,
            )
            continue
        if line == "/usage":
            print(json.dumps(session.usage.to_dict()), file=sys.stderr)
            continue
        if line.startswith("/mode"):
            parts = line.split()
            if len(parts) == 2 and parts[1] in MODES:
                agent.policy.mode = parts[1]  # type: ignore[assignment]
                print(f"mode is now {parts[1]}", file=sys.stderr)
            else:
                print(f"usage: /mode {{{','.join(MODES)}}}", file=sys.stderr)
            continue
        result = agent.run(session, line)
        if result.status != "done":
            _err(f"{result.status}: {result.error}")


if __name__ == "__main__":
    sys.exit(main())
