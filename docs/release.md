# Release procedure

Releases are GitHub Releases built by `.github/workflows/release.yml` from a `vX.Y.Z` tag. PyPI publishing is not automated (no credentials are available to the project; see the `needs-human` issue if one is open).

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
5. Verify the release by installing from it:
   ```bash
   uv tool install https://github.com/yezhoufan2005/CLI-Agent/releases/download/vX.Y.Z/cli_agent-X.Y.Z-py3-none-any.whl
   ```

## Publishing to PyPI (single manual step, owner only)

```bash
gh release download vX.Y.Z -p '*.whl' -p '*.tar.gz' -D dist/
uv publish dist/*        # needs a PyPI token: UV_PUBLISH_TOKEN
```

## Compatibility policy (0.x)

- The CLI flags, config keys, exit codes, `--json` event names, and session file format are the public interface.
- Before 1.0, a minor version may change them. Every such change is listed in CHANGELOG under "Changed" or "Removed".
- Session files carry a `version` field. A build that cannot read an older session version says so instead of misreading it.
