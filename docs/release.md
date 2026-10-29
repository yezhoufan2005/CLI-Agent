# Release procedure

Releases are built by `.github/workflows/release.yml` from a `vX.Y.Z` tag. One tag publishes both the GitHub Release and the PyPI package [`cli-agent-yzf`](https://pypi.org/project/cli-agent-yzf/) (the name `cli-agent` is taken on PyPI; the command is still `cli-agent`). A PyPI version cannot be uploaded twice, so never re-push or reuse a tag; fix forward with a new patch version.

## Steps

1. Check the release gates in `docs/product.md` § "Release gates" and record the evidence in `docs/progress.md`:
   - CI is green on the release commit.
   - `./scripts/e2e.sh` passes.
   - There are no open `severity:high` issues.
   - An independent security review was done.
2. On a branch, set the version in `pyproject.toml` and `src/cli_agent/__init__.py`, rename `## Unreleased` in `CHANGELOG.md` to `## X.Y.Z — YYYY-MM-DD`, and add an empty `## Unreleased`. Merge via PR.
3. Tag the merged commit on `main`, then push the tag:
   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z
   ```
4. The workflow then does the following:
   - Checks that the tag matches the package version.
   - Runs `scripts/check.sh`.
   - Builds the sdist and wheel, and smoke-tests the wheel.
   - Publishes the Release with the artifacts, `SHA256SUMS`, and the CHANGELOG section.
   - The `pypi` job uploads the same sdist and wheel to PyPI.
5. Verify both installs:
   ```bash
   uv tool install https://github.com/yezhoufan2005/CLI-Agent/releases/download/vX.Y.Z/cli_agent_yzf-X.Y.Z-py3-none-any.whl
   uv tool install cli-agent-yzf==X.Y.Z
   ```

## PyPI publishing

The `pypi` job uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/): PyPI trusts OIDC tokens from this repository's `release.yml` running in the GitHub environment `pypi`. No PyPI token exists in the repository or its secrets. Renaming `release.yml`, renaming the `pypi` environment, or moving the publish step to another job breaks publishing until the owner updates the publisher on PyPI.

If the `pypi` job fails after the GitHub Release was created, fix the cause and re-run only that job from the Actions page (`gh run rerun RUN_ID --failed`). Do not delete and re-push the tag.

## Compatibility policy (0.x)

- The CLI flags, config keys, exit codes, `--json` event names, and session file format are the public interface.
- Before 1.0, a minor version may change them. Every such change is listed in CHANGELOG under "Changed" or "Removed".
- Session files carry a `version` field. A build that cannot read an older session version says so instead of misreading it.
