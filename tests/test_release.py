"""Release tooling: version consistency and changelog extraction."""

import subprocess
import tomllib
from pathlib import Path

import cli_agent

ROOT = Path(__file__).resolve().parent.parent


def test_versions_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["version"] == cli_agent.__version__


def test_release_notes_extracts_current_version() -> None:
    # The release workflow extracts the section for the tagged version, so the
    # current version must always have a non-empty CHANGELOG section.
    r = subprocess.run(
        [str(ROOT / "scripts/release-notes.sh"), cli_agent.__version__],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "### " in r.stdout and "\n## " not in r.stdout


def test_release_notes_missing_version_fails() -> None:
    r = subprocess.run(
        [str(ROOT / "scripts/release-notes.sh"), "99.99.99"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0 and "no CHANGELOG section" in r.stderr
