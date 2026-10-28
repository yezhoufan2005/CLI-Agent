import pytest

from cli_agent.permissions import PermissionPolicy, dangerous_reason


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /",
        "rm -rf / --no-preserve-root",
        "sudo rm -rf ~",
        "rm -fr $HOME",
        "rm -r -f /*",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda bs=1M",
        "git push --force origin main",
        "git push -f",
        "echo x && shutdown -h now",
        ":(){ :|:& };:",
    ],
)
def test_dangerous_commands_detected(cmd: str) -> None:
    assert dangerous_reason(cmd) is not None


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf build/",
        "rm -rf ./dist /tmp/x/cache",
        "rm file.txt",
        "git push origin feature",
        "dd if=a of=b",
        "pytest -k shutdown_handler",
        "grep -r reboot docs/",
    ],
)
def test_ordinary_commands_not_flagged(cmd: str) -> None:
    # The guard must not be so noisy that users turn the agent off.
    assert dangerous_reason(cmd) is None


def test_mode_defaults() -> None:
    ask = PermissionPolicy(mode="ask")
    assert ask.check("read_file", "read", {"path": "a"}).decision == "allow"
    assert ask.check("edit_file", "write", {"path": "a"}).decision == "ask"
    assert ask.check("run_shell", "execute", {"command": "ls"}).decision == "ask"
    ae = PermissionPolicy(mode="auto-edit")
    assert ae.check("edit_file", "write", {"path": "a"}).decision == "allow"
    assert ae.check("run_shell", "execute", {"command": "ls"}).decision == "ask"
    auto = PermissionPolicy(mode="auto")
    assert auto.check("run_shell", "execute", {"command": "ls"}).decision == "allow"
    ro = PermissionPolicy(mode="read-only")
    assert ro.check("write_file", "write", {"path": "a"}).decision == "deny"
    assert ro.check("run_shell", "execute", {"command": "ls"}).decision == "deny"


def test_read_only_not_widened_by_allow_rules() -> None:
    ro = PermissionPolicy(mode="read-only", allow=["run_shell"])
    assert ro.check("run_shell", "execute", {"command": "ls"}).decision == "deny"


def test_rules_precedence() -> None:
    p = PermissionPolicy(
        mode="ask", allow=["run_shell:pytest*", "run_shell:git *"], deny=["run_shell:git push*"]
    )
    assert p.check("run_shell", "execute", {"command": "pytest -q"}).decision == "allow"
    assert p.check("run_shell", "execute", {"command": "git status"}).decision == "allow"
    assert p.check("run_shell", "execute", {"command": "git push origin x"}).decision == "deny"
    assert p.check("run_shell", "execute", {"command": "make"}).decision == "ask"


def test_guard_beats_auto_and_allow() -> None:
    p = PermissionPolicy(mode="auto", allow=["run_shell"])
    v = p.check("run_shell", "execute", {"command": "rm -rf /"})
    assert v.decision == "deny" and "built-in guard" in v.reason


def test_remember_shell_is_exact() -> None:
    p = PermissionPolicy(mode="ask")
    rule = p.remember("run_shell", {"command": "ls *.py"})
    assert p.check("run_shell", "execute", {"command": "ls *.py"}).decision == "allow"
    # A glob metacharacter in the approved command must not widen the rule.
    assert p.check("run_shell", "execute", {"command": "ls x.py; rm -rf data"}).decision == "ask"
    assert rule.startswith("run_shell:")
    p.remember("edit_file", {"path": "a"})
    assert p.check("edit_file", "write", {"path": "zzz"}).decision == "allow"
