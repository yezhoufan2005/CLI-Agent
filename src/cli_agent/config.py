"""Configuration: defaults < user config file < environment < command-line flags.

Only the *user* config file (``~/.config/cli-agent/config.toml``) may grant
permissions. Repository content is untrusted, so nothing inside the workspace
can widen what the agent is allowed to do.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli_agent.mcp import ServerConfig, parse_server_configs
from cli_agent.permissions import MODES, Mode
from cli_agent.sandbox import SANDBOX_MODES

DEFAULT_MODEL = "claude-sonnet-4-6"
PROVIDERS = ("anthropic", "openai", "fake")


class ConfigError(Exception):
    pass


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "cli-agent" / "config.toml"


@dataclass
class Config:
    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    base_url: str = ""
    """Empty means the provider's default endpoint."""
    api_key: str | None = None
    auth_token: str | None = None
    mode: Mode = "ask"
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    max_turns: int = 50
    max_tokens: int = 8192
    shell_timeout: float = 120.0
    context_chars: int = 400_000
    """History budget (~4 chars per token); older tool output is elided past it."""
    sandbox: str = "off"
    sandbox_network: bool = False
    mcp_servers: list[ServerConfig] = field(default_factory=list)
    source: str = "defaults"

    def redacted(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": "set" if self.api_key else "unset",
            "auth_token": "set" if self.auth_token else "unset",
            "mode": self.mode,
            "allow": self.allow,
            "deny": self.deny,
            "max_turns": self.max_turns,
            "max_tokens": self.max_tokens,
            "shell_timeout": self.shell_timeout,
            "context_chars": self.context_chars,
            "sandbox": self.sandbox,
            "sandbox_network": self.sandbox_network,
            "mcp_servers": [s.name for s in self.mcp_servers],
            "source": self.source,
        }


_KEYS = {
    "provider": str,
    "model": str,
    "base_url": str,
    "mode": str,
    "allow": list,
    "deny": list,
    "max_turns": int,
    "max_tokens": int,
    "shell_timeout": float,
    "context_chars": int,
    "sandbox": str,
    "sandbox_network": bool,
}


def _apply_file(cfg: Config, path: Path) -> None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{path}: invalid TOML: {err}") from err
    for key, value in data.items():
        if key in ("api_key", "auth_token"):
            raise ConfigError(
                f"{path}: do not store {key} in the config file; use the environment "
                "(ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN, or OPENAI_API_KEY)"
            )
        if key == "mcp_servers":
            try:
                cfg.mcp_servers = parse_server_configs(value, str(path))
            except ValueError as err:
                raise ConfigError(str(err)) from err
            continue
        expected = _KEYS.get(key)
        if expected is None:
            raise ConfigError(f"{path}: unknown key {key!r}")
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected) or (isinstance(value, bool) and expected is not bool):
            raise ConfigError(f"{path}: {key} must be a {expected.__name__}")
        if isinstance(value, list) and not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{path}: {key} must be a list of strings")
        setattr(cfg, key, value)
    cfg.source = str(path)


def load_config(
    path: Path | None = None,
    environ: dict[str, str] | None = None,
    provider: str | None = None,
) -> Config:
    env = os.environ if environ is None else environ
    cfg = Config()
    config_path = path or Path(env.get("CLI_AGENT_CONFIG") or default_config_path())
    if config_path.exists():
        _apply_file(cfg, config_path)
    elif path is not None:
        raise ConfigError(f"config file {path} does not exist")
    # The provider must be settled first: it decides which credentials to read.
    if provider or env.get("CLI_AGENT_PROVIDER"):
        cfg.provider = provider or env["CLI_AGENT_PROVIDER"]
    # Credentials and endpoint come from the chosen provider's usual variables.
    if cfg.provider == "openai":
        if env.get("OPENAI_BASE_URL"):
            cfg.base_url = env["OPENAI_BASE_URL"]
        cfg.api_key = env.get("OPENAI_API_KEY") or None
    else:
        if env.get("ANTHROPIC_BASE_URL"):
            cfg.base_url = env["ANTHROPIC_BASE_URL"]
        cfg.api_key = env.get("ANTHROPIC_API_KEY") or None
        cfg.auth_token = env.get("ANTHROPIC_AUTH_TOKEN") or None
    if env.get("CLI_AGENT_MODEL"):
        cfg.model = env["CLI_AGENT_MODEL"]
    if env.get("CLI_AGENT_SANDBOX"):
        cfg.sandbox = env["CLI_AGENT_SANDBOX"]
    if env.get("CLI_AGENT_MODE"):
        cfg.mode = env["CLI_AGENT_MODE"]  # type: ignore[assignment]
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.mode not in MODES:
        raise ConfigError(f"invalid mode {cfg.mode!r}; choose one of {', '.join(MODES)}")
    if cfg.sandbox not in SANDBOX_MODES:
        raise ConfigError(
            f"invalid sandbox {cfg.sandbox!r}; choose one of {', '.join(SANDBOX_MODES)}"
        )
    if cfg.provider not in PROVIDERS:
        raise ConfigError(f"unknown provider {cfg.provider!r}")
    if cfg.context_chars < 10_000:
        raise ConfigError("context_chars must be at least 10000")
    if cfg.max_turns < 1 or cfg.max_tokens < 1 or cfg.shell_timeout <= 0:
        raise ConfigError("max_turns, max_tokens and shell_timeout must be positive")
