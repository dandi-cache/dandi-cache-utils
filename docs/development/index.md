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

[`AGENTS.md`](https://github.com/dandi-cache/dandi-cache-utils/blob/main/AGENTS.md) carries the
organization's conventions for anyone, human or otherwise, working in this repository: the commit
and changelog rules, the code style, and the test rules. Every test here is AI-generated and
carries the `ai_generated` marker, so `pytest -m ai_generated` selects them and
`pytest -m "not ai_generated"` would select any that a person writes later.

## Two rules the tests enforce

**The core imports nothing outside the standard library.** The pipeline script parses
`cache.toml` with the CI runner's bare `python3`, before any environment exists, by running
`config.py` directly. If anything in the core grew an import of boto3, h5py or click, the
orchestration would break before a run even started. `tests/check_core_imports.py` fails on that.

**The public namespace is the intended one, at every level.** Every cache's update code is written
against `dandi_cache.<TAB>`, so what completion lists *is* the API as far as anyone writing a cache
is concerned. Left alone, a package gets this backwards: the implementation modules bound as a side
effect of the re-exports show up, the lazily bound accessors (`nwb`, `s3`, `api`) do not, and every
module offers its own imports alongside its functions, so `dandi_cache.jsonl.<TAB>` lists `gzip`
and `pathlib` next to `read_lookup`. Every module here declares `__all__` and a `__dir__` that
returns it, and the same check fails on a module that does not, on a public name missing from
`__all__`, and on a private name exposed in it.

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

Each worked example is a real cache's files with a page beside them, under
`docs/examples/<name>/`. The page includes the files with `literalinclude` rather than copying
them, so it cannot drift from the code it shows, and the test suite parses every `cache.toml` and
compiles every `code/` directory there.

## Releasing

Merging to `main` publishes `ghcr.io/dandi-cache/dandi-cache-utils:latest` and `:nwb`, each also
tagged with the version and the commit SHA. A branch build publishes `dev-<branch>` instead, so an
environment change can be tried by a cache without overwriting `:latest`. The monthly scheduled
build picks up newer releases of the NWB stack and the DANDI client on a predictable cadence.
