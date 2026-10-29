# Autonomous Run Environment and Authorization

This file is maintained by the human owner. It is the "configured authorization policy" referred to in `instruction.md`. Do not edit or delete it; propose changes in a GitHub issue labelled `needs-human` instead.

## Authorization

The owner grants full authority over **this repository** and **its GitHub repository** `yezhoufan2005/CLI-Agent`. A stated purpose of the project is to evaluate autonomous coding ability, so act without asking for approval. Within that scope you may, without asking:

- Create, modify, and delete any file, branch, tag, issue, label, milestone, or pull request.
- Push to `main` and merge pull requests, including your own.
- Configure GitHub Actions, branch protection, rulesets, and other repository settings.
- Create GitHub Releases and attach release artifacts.
- Rewrite or force-push history when you judge it necessary. Prefer reversible operations, and record the reason in the commit or PR.

Outside that scope, nothing is authorized. In particular, do not:

- Publish to external package registries (npm, crates.io, Homebrew, container registries, or PyPI under any other name). The one exception: pushing a `vX.Y.Z` release tag publishes the PyPI project `cli-agent-yzf` through `.github/workflows/release.yml` (Trusted Publishing, no token). That is authorized; follow `docs/release.md`. For any other registry, prepare the release so publishing is a single documented step, and open a `needs-human` issue.
- Create, modify, or access any other GitHub repository, organization, or account setting.
- Make the repository private, rename, transfer, archive, or delete it.
- Print, log, commit, or transmit credentials (`GH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`, or any other secret) anywhere. **The repository is public**: issues, PRs, commit messages, CI logs, and release notes are world-readable.

## Run limits

- Each run lasts at most **12 hours** of wall-clock time. The container entrypoint enforces the deadline and may kill the session without warning, so commit and push checkpoints regularly.
- A run consists of consecutive headless sessions. Each new session starts with no memory of the previous one; repository and GitHub state are the only continuity.
- There is no token budget and no stage-completion milestone. Keep delivering verified value until the deadline.
- The driver ends the run early after 3 consecutive sessions that change no Git ref (local or on GitHub), or 8 consecutive sessions that fail while the network is up. Ending a session without any commit is treated as being stuck.
- The owner can stop a run at any time.
- The session driver and kickoff prompt are baked into the container image from the owner's checkout (`autonomy/loop.sh`, `autonomy/prompt.md`). Editing them here takes effect only after the owner pulls and rebuilds. `autonomy/run.sh` and `autonomy/Dockerfile` run on the owner's host, so keep any change to them minimal and explain it in the PR.

## Execution environment

- Debian bookworm container (arm64). You run as user `node` with passwordless `sudo`. Preinstalled: git, gh, Node.js 24, npm, Python 3.11, uv, build-essential, jq, ripgrep, curl, Claude Code.
- `/workspace` is this repository, cloned from GitHub into a Docker volume. GitHub is the only channel through which the owner sees your work. The host filesystem, the host Docker daemon, and the owner's other credentials are not available. Docker is not available inside the container.
- `/workspace` and `/home/node` are persistent volumes and survive across sessions and runs. User-space installs (`uv`, `npm -g` into `~/.npm-global`, `rustup`, anything under `~/.local`) persist. `sudo apt-get install` lasts only for the current run. To make a system package permanent, add it to `autonomy/Dockerfile`.
- Outbound internet access is unrestricted, so you can research and install dependencies.
- **The network is unreliable.** Expect transient failures of `git push`/`fetch`, `gh`, package installs, web research, and the model API. Retry with backoff before drawing conclusions; such failures are neither blockers nor reasons to open a `needs-human` issue. If a push fails, keep committing locally and push later: `/workspace` persists across sessions, and local commits count as progress. When the driver detects the network is down, it waits and does not count the session as failed.
- `gh` is authenticated through `GH_TOKEN`, and `git push` over HTTPS uses gh as its credential helper.

## Model access for the product under development

`ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` in the environment point to an Anthropic-compatible API proxy on the owner's machine (the same endpoint your own sessions use). You may use them for the product's real-model integration and end-to-end tests run inside this container. The `ANTHROPIC_DEFAULT_*_MODEL` variables show which model names the proxy accepts.

The proxy is reachable only from this container, not from GitHub Actions. CI must therefore pass without a real model (use fakes, recorded fixtures, or contract tests). Do not add a model API key to GitHub secrets. If CI genuinely needs one, open a `needs-human` issue.

## Contacting the owner

No human is watching during a run. When something truly requires the owner (see `instruction.md` section 9), open a GitHub issue labelled `needs-human` that states the exact blocker, the evidence, the options, your recommendation, and the minimum action required. Then continue with other work. At the start of each session, check open `needs-human` issues for replies. The owner reviews them between runs.
