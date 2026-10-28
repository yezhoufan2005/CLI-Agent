"""Sandbox tests. Real-isolation tests run only where bubblewrap works (CI installs
it on Linux); elsewhere the fallback and fail-closed behaviour is tested."""

import os
from pathlib import Path

import pytest

from cli_agent import sandbox as sb
from cli_agent.tools import ToolContext
from cli_agent.tools.shell import RunShell

AVAILABLE = sb.probe() is None


def test_sandbox_available_when_ci_requires_it() -> None:
    # CI sets this on Linux so the real-isolation tests cannot be skipped silently.
    if os.environ.get("CLI_AGENT_REQUIRE_SANDBOX_TESTS") == "1":
        assert AVAILABLE, sb.probe()


def test_wrap_layout(tmp_path: Path) -> None:
    argv = sb.Sandbox(root=tmp_path).wrap(["bash", "-c", "ls"])
    root = str(tmp_path.resolve())
    assert argv[0] == "bwrap" and argv[-3:] == ["bash", "-c", "ls"]
    joined = " ".join(argv)
    assert "--ro-bind / /" in joined and f"--bind {root} {root}" in joined
    assert "--unshare-net" in argv and "--die-with-parent" in argv
    # The workspace bind must come after the tmpfs mounts that could hide it.
    assert argv.index("--bind") > max(i for i, a in enumerate(argv) if a == "--tmpfs")
    assert "--unshare-net" not in sb.Sandbox(root=tmp_path, network=True).wrap(["true"])


def test_off_never_probes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sb, "probe", lambda: pytest.fail("probed"))
    assert sb.resolve("off", tmp_path, False) == (None, None)


def test_unavailable_auto_warns_required_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sb, "probe", lambda: "no userns")
    box, note = sb.resolve("auto", tmp_path, False)
    assert box is None and note is not None and "unsandboxed" in note
    with pytest.raises(sb.SandboxUnavailable, match="no userns"):
        sb.resolve("required", tmp_path, False)


def test_probe_reports_missing_binary() -> None:
    reason = sb.probe(bwrap="/nonexistent/bwrap")
    assert reason is not None


@pytest.mark.skipif(not AVAILABLE, reason=f"bubblewrap unusable here: {sb.probe()}")
class TestRealSandbox:
    def run(self, tmp_path: Path, command: str) -> str:
        box, _ = sb.resolve("required", tmp_path, False)
        ctx = ToolContext(root=tmp_path, sandbox=box)
        return RunShell().run({"command": command, "timeout": 30}, ctx).content

    def test_workspace_writable(self, tmp_path: Path) -> None:
        out = self.run(tmp_path, "echo hi > inside.txt && cat inside.txt")
        assert "hi" in out and (tmp_path / "inside.txt").read_text() == "hi\n"

    def test_outside_read_only(self, tmp_path: Path) -> None:
        target = tmp_path.parent / f"{tmp_path.name}-outside.txt"
        out = self.run(tmp_path, f"echo x > {target}; echo rc=$?")
        assert not target.exists() and "rc=0" not in out

    def test_home_hidden(self, tmp_path: Path) -> None:
        home = Path.home()
        marker = home / ".cli-agent-sandbox-test"
        marker.write_text("secret")
        try:
            out = self.run(tmp_path, f"cat {marker}; echo rc=$?")
            assert "secret" not in out
        finally:
            marker.unlink()

    def test_no_network(self, tmp_path: Path) -> None:
        out = self.run(tmp_path, "cat /sys/class/net/*/operstate 2>/dev/null; ls /sys/class/net")
        assert out.split()[-1] == "lo" or ("lo" in out and "eth0" not in out)
