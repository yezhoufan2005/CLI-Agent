#!/usr/bin/env python3
"""Run cli-agent on SWE-bench Lite instances from sympy/sympy, without Docker.

    uv run python scripts/swebench_sympy.py --data lite.parquet --repo SYMPY_CLONE \
        [--n 20] [--seed 1] [-j 4] [--model M] [--gold] [--report out.json]

SymPy is pure Python (one dependency, mpmath), so each instance can run in a
git worktree with a shared Python 3.9 venv, matching SWE-bench's sympy
environment (python 3.9, mpmath 1.3.0). The agent sees only the problem
statement; the hidden test patch is applied afterwards and the instance is
"resolved" if every FAIL_TO_PASS and PASS_TO_PASS test passes, as in
SWE-bench. `--gold` applies the reference patch instead of running the agent,
to validate the harness itself.

Needs: `uv python install 3.9`, `pip install pyarrow` (via `uv run --with pyarrow`),
and real-model credentials unless --gold.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = """\
You are working in a checkout of the sympy repository. Resolve this GitHub issue by \
changing the library source code (not the tests). Hidden tests will check the fix.

<issue>
{problem}
</issue>

Investigate the relevant code first, make a minimal fix, and check it by running a short \
python script with `{python}` (it has sympy's dependencies). Do not modify or add test files."""


def sh(
    cmd: list[str], cwd: Path, timeout: int = 600, **kw: object
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # type: ignore[call-overload,no-any-return]
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False, **kw
    )


def make_venv(base: Path) -> Path:
    venv = base / "venv39"
    if not (venv / "bin" / "python").exists():
        sh(["uv", "venv", "-q", "--python", "3.9", str(venv)], base)
        sh(
            ["uv", "pip", "install", "-q", "--python", str(venv / "bin/python"), "mpmath==1.3.0"],
            base,
        )
    return venv / "bin" / "python"


def test_files(test_patch: str) -> list[str]:
    return sorted(set(re.findall(r"^\+\+\+ b/(\S+)", test_patch, re.M)))


def run_tests(work: Path, python: Path, files: list[str]) -> dict[str, str]:
    """Run sympy's own test runner; return {test_name: 'ok'|'fail'}."""
    r = sh([str(python), "bin/test", "-C", "--verbose", *files], work, timeout=1800)
    status: dict[str, str] = {}
    for line in r.stdout.splitlines():
        m = re.match(r"^(test_\w+)\s+(ok|F|E|f|s|X|w|T)\b", line.strip())
        if m:
            status[m.group(1)] = "ok" if m.group(2) in ("ok", "f", "X") else "fail"
    return status


def run_one(inst: dict, args: argparse.Namespace, python: Path, state: Path) -> dict:  # type: ignore[type-arg]
    iid = inst["instance_id"]
    with tempfile.TemporaryDirectory(prefix=f"{iid}-") as tmp:
        work = Path(tmp) / "repo"
        sh(
            [
                "git",
                "-C",
                str(args.repo),
                "worktree",
                "add",
                "-q",
                "--detach",
                str(work),
                inst["base_commit"],
            ],
            args.repo,
        )
        try:
            start = time.monotonic()
            result: dict = {}
            if args.gold:
                (Path(tmp) / "gold.diff").write_text(inst["patch"])
                sh(["git", "apply", str(Path(tmp) / "gold.diff")], work)
            else:
                prompt = PROMPT.format(problem=inst["problem_statement"], python=python)
                agent = sh(
                    [
                        sys.executable,
                        "-m",
                        "cli_agent",
                        "-C",
                        str(work),
                        "--mode",
                        "auto",
                        "--model",
                        args.model,
                        "--max-turns",
                        "60",
                        "--json",
                        prompt,
                    ],
                    ROOT,
                    timeout=args.timeout,
                    env={**_env(), "XDG_STATE_HOME": str(state), "CLI_AGENT_CONFIG": "/dev/null"},
                )
                for line in agent.stdout.splitlines():
                    if '"event": "result"' in line:
                        result = json.loads(line)
            secs = time.monotonic() - start
            # Discard any test edits the agent made, then apply the hidden tests.
            files = test_files(inst["test_patch"])
            sh(["git", "checkout", "-q", "HEAD", "--", *files], work)
            (Path(tmp) / "test.diff").write_text(inst["test_patch"])
            applied = sh(["git", "apply", str(Path(tmp) / "test.diff")], work)
            status = run_tests(work, python, files) if applied.returncode == 0 else {}
            f2p = json.loads(inst["FAIL_TO_PASS"])
            p2p = json.loads(inst["PASS_TO_PASS"])
            f2p_ok = sum(status.get(t) == "ok" for t in f2p)
            p2p_ok = sum(status.get(t) == "ok" for t in p2p)
            changed = sh(["git", "diff", "--stat", "HEAD"], work).stdout.strip().splitlines()
            return {
                "instance_id": iid,
                "resolved": applied.returncode == 0 and f2p_ok == len(f2p) and p2p_ok == len(p2p),
                "fail_to_pass": f"{f2p_ok}/{len(f2p)}",
                "pass_to_pass": f"{p2p_ok}/{len(p2p)}",
                "status": result.get("status", "gold" if args.gold else None),
                "turns": result.get("turns"),
                "input_tokens": (result.get("usage") or {}).get("input_tokens"),
                "secs": round(secs, 1),
                "diffstat": changed[-1] if changed else "",
            }
        finally:
            sh(["git", "-C", str(args.repo), "worktree", "remove", "--force", str(work)], args.repo)


def _env() -> dict[str, str]:
    import os

    return dict(os.environ)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--gold", action="store_true")
    ap.add_argument("--ids", nargs="*", help="run exactly these instance ids")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    import pyarrow.parquet as pq

    rows = [r for r in pq.read_table(args.data).to_pylist() if r["repo"] == "sympy/sympy"]
    if args.ids:
        chosen = [r for r in rows if r["instance_id"] in set(args.ids)]
    else:
        rows.sort(key=lambda r: r["instance_id"])
        random.Random(args.seed).shuffle(rows)  # noqa: S311 - reproducible sampling
        chosen = sorted(rows[: args.n], key=lambda r: r["instance_id"])
    python = make_venv(args.repo.parent)
    results = []
    with tempfile.TemporaryDirectory() as state, ThreadPoolExecutor(args.jobs) as pool:
        futs = [pool.submit(run_one, inst, args, python, Path(state)) for inst in chosen]
        for fut in futs:
            try:
                row = fut.result()
            except subprocess.TimeoutExpired:
                row = {"instance_id": "?", "resolved": False, "status": "timeout"}
            results.append(row)
            mark = "RESOLVED" if row["resolved"] else "unresolved"
            print(
                f"{mark} {row['instance_id']} f2p={row.get('fail_to_pass')} "
                f"p2p={row.get('pass_to_pass')} status={row.get('status')} "
                f"turns={row.get('turns')} secs={row.get('secs')}",
                flush=True,
            )
    n_ok = sum(r["resolved"] for r in results)
    label = "gold" if args.gold else args.model
    print(f"swebench-lite-sympy {label} seed={args.seed} n={len(results)} resolved={n_ok}")
    if args.report:
        args.report.write_text(
            json.dumps({"model": label, "seed": args.seed, "results": results}, indent=1)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
