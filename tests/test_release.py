"""Release tooling: version consistency and changelog extraction."""

import subprocess
import tomllib
from pathlib import Path

import cli_agent

ROOT = Path(__file__).resolve().parent.parent


def test_versions_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["version"] == cli_agent.__version__


def test_release_notes_extracts_section() -> None:
    r = subprocess.run(
        [str(ROOT / "scripts/release-notes.sh"), "Unreleased"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0 and "### " in r.stdout and "## " not in r.stdout.replace("### ", "")


def test_release_notes_missing_version_fails() -> None:
    r = subprocess.run(
        [str(ROOT / "scripts/release-notes.sh"), "99.99.99"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0 and "no CHANGELOG section" in r.stderr
