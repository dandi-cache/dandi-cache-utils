# Development

A change here reaches every cache in the organization at once. That is the point, and it is also
the risk, so the tests cover the behaviours that differed between repositories before: batch
ordering, what a failure means, and whether testing mode can touch the real cache.

## Running the checks

```bash
uv run --with ".[test]" pytest
uv run --with ".[test]" python tests/check_core_imports.py
python -m sphinx -b html -W docs docs/_build/html
pre-commit run --all-files
```

The formatters and linters -- black, ruff, shellcheck and codespell -- are pre-commit hooks, so
they run on every pull request through `pre-commit.ci` rather than in the test matrix. Only checks
that need a particular interpreter belong there.

## Two rules the tests enforce

**The core imports nothing outside the standard library.** The pipeline script parses
`cache.toml` with the CI runner's bare `python3`, before any environment exists, by running
`config.py` directly. If anything in the core grew an import of boto3, h5py or click, the
orchestration would break before a run even started. `tests/check_core_imports.py` fails on that.

**The public namespace is the intended one.** Every cache's update code is written against
`dandi_cache.<TAB>`, so what completion lists *is* the API as far as anyone writing a cache is
concerned. Left alone, a package gets this backwards: the implementation modules bound as a side
effect of the re-exports show up, the lazily bound accessors (`nwb`, `s3`, `api`) do not, and each
module offers its own imports alongside its functions. The same check fails on any of that.

## Versioning

`[project] version` in `pyproject.toml` is the only place the version is written. `__version__`
resolves from it — from the installed distribution's metadata, or, for the copy vendored into the
image and imported straight from `src/`, by reading the `pyproject.toml` shipped beside it.

Bump it in any pull request that changes `src/` or `pyproject.toml`: those are the paths
that reach the published image, and a release publishes the version as its own image tag, which a
cache may have pinned. A CI- or documentation-only change needs no bump. The `Version Check`
workflow enforces this, and `CHANGELOG.md` records what changed.

## Documentation

The pages are built with Sphinx and published by Read the Docs from `docs/.readthedocs.yaml`.
`fail_on_warning` is on and Read the Docs builds every pull request, so a broken cross-reference
fails there rather than after merge. Build it the same way locally with the command above before
pushing.

The examples are included from `examples/` with `literalinclude` rather than copied, so a page
cannot drift from the code it shows.

## Releasing

Merging to `main` publishes `ghcr.io/dandi-cache/dandi-cache-utils:latest` and `:nwb`, each also
tagged with the version and the commit SHA. A branch build publishes `dev-<branch>` instead, so an
environment change can be tried by a cache without overwriting `:latest`. The monthly scheduled
build picks up newer releases of the NWB stack and the DANDI client on a predictable cadence.
