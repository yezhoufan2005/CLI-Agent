#!/usr/bin/env python3
"""Exercism-Python benchmark: the agent solves exercises from instructions alone;
hidden unit tests decide pass/fail.

    uv run python scripts/bench.py --exercises DIR [--model M] [--n 20] [--seed 1] [-j 4]

DIR is exercises/practice of a checkout of https://github.com/exercism/python.
For each exercise the agent gets the instructions and the stub file (not the
tests, not .meta); afterwards the hidden test file is copied in and run with
pytest. Needs real-model credentials. Prints one line per exercise and a
summary, and writes a JSON report.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = (
    "Solve this programming exercise. The instructions are in INSTRUCTIONS.md; implement the "
    "solution in {stub}, keeping the existing function and class names. Hidden unit tests will "
    "check it, so handle the edge cases and error messages the instructions describe. You may "
    "write your own quick checks with python3, but do not leave extra files behind."
)


def pick(exercises: Path, n: int, seed: int) -> list[Path]:
    candidates = sorted(
        d
        for d in exercises.iterdir()
        if (d / ".meta" / "config.json").is_file() and (d / ".docs" / "instructions.md").is_file()
    )
    random.Random(seed).shuffle(candidates)  # noqa: S311 - reproducible sampling
    return sorted(candidates[:n])


def run_one(ex: Path, model: str, state: Path, timeout: int) -> dict[str, object]:
    meta = json.loads((ex / ".meta" / "config.json").read_text())
    stubs = meta["files"]["solution"]
    tests = meta["files"]["test"]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for stub in stubs:
            shutil.copy(ex / stub, work / stub)
        docs = (ex / ".docs" / "instructions.md").read_text()
        extra = ex / ".docs" / "instructions.append.md"
        if extra.exists():
            docs += "\n\n" + extra.read_text()
        (work / "INSTRUCTIONS.md").write_text(docs)
        start = time.monotonic()
        agent = subprocess.run(
            [
                sys.executable,
                "-m",
                "cli_agent",
                "-C",
                str(work),
                "--mode",
                "auto",
                "--model",
                model,
                "--max-turns",
                "30",
                "--json",
                PROMPT.format(stub=", ".join(stubs)),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=ROOT,
            env={**_env(), "XDG_STATE_HOME": str(state), "CLI_AGENT_CONFIG": "/dev/null"},
        )
        secs = time.monotonic() - start
        result: dict[str, object] = {}
        for line in agent.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "result":
                result = event
        for t in tests:
            shutil.copy(ex / t, work / t)
        test = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-x",
                "--no-header",
                "-p",
                "no:cacheprovider",
                *tests,
            ],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        usage = result.get("usage") or {}
        return {
            "exercise": ex.name,
            "passed": test.returncode == 0,
            "agent_exit": agent.returncode,
            "status": result.get("status"),
            "turns": result.get("turns"),
            "secs": round(secs, 1),
            "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
            "test_tail": test.stdout.strip().splitlines()[-1:] if test.stdout else [],
        }


def _env() -> dict[str, str]:
    import os

    return dict(os.environ)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exercises", type=Path, required=True)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()
    chosen = pick(args.exercises, args.n, args.seed)
    with tempfile.TemporaryDirectory() as state, ThreadPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(run_one, ex, args.model, Path(state), args.timeout) for ex in chosen]
        rows = []
        for fut in futures:
            try:
                row = fut.result()
            except subprocess.TimeoutExpired as err:
                row = {"exercise": "?", "passed": False, "status": f"timeout: {err.cmd[-1][:40]}"}
            rows.append(row)
            mark = "PASS" if row["passed"] else "FAIL"
            print(
                f"{mark} {row['exercise']} status={row.get('status')} turns={row.get('turns')} "
                f"secs={row.get('secs')}",
                flush=True,
            )
    passed = sum(1 for r in rows if r["passed"])
    print(
        f"bench model={args.model} seed={args.seed} n={len(rows)} pass={passed} "
        f"rate={passed / max(len(rows), 1):.0%}"
    )
    if args.report:
        args.report.write_text(
            json.dumps({"model": args.model, "seed": args.seed, "results": rows}, indent=1)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
