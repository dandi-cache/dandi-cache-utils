# Development

A change here reaches every cache in the organization at once.
That is the point, and it is also the risk, so the tests cover the behaviours that differed between repositories before: batch ordering, what a failure means, and whether testing mode can touch the real cache.

## Running the checks

```bash
uv run --with ".[test]" pytest
uv run --with ".[test]" python tests/check_core_imports.py
python -m sphinx -b html -W docs docs/_build/html
pre-commit run --all-files
```

The formatters and linters -- black, ruff, shellcheck and codespell -- are pre-commit hooks, so they run on every pull request through `pre-commit.ci` rather than in the test matrix.
Only checks that need a particular interpreter belong there.

[`AGENTS.md`](https://github.com/dandi-cache/dandi-cache-utils/blob/main/AGENTS.md) carries the organization's conventions for anyone, human or otherwise, working in this repository: the commit and changelog rules, the code style, and the test rules.
Every test here is AI-generated and carries the `ai_generated` marker, so `pytest -m ai_generated` selects them and `pytest -m "not ai_generated"` would select any that a person writes later.

## Two rules the tests enforce

These two run in the image as well as on the runner.
The `Test` matrix proves the sources work on three interpreters; the image build runs the same suite against the library as *installed in the image*, which is the artifact every cache actually runs.
That second run is where the first rule below is genuinely tested: on the runner boto3, h5py and pynwb are not installed at all, so the package could hardly import them, while in the image they are.

**Importing the package needs none of the extras.** The `:latest` base image installs `[s3,archive]` and not the NWB stack, so a module-level `import pynwb` anywhere the import graph reaches would break every cache built on it.
`api`, `nwb` and `s3` therefore keep their third-party imports inside the functions that use them, which is also what makes the plain `from . import api, nwb, s3` in `__init__.py` free.

**The bootstrap needs nothing at all.** The pipeline parses `cache.toml` with the CI runner's bare `python3` before any environment exists, by running `_config.py` as a plain script.
That module imports nothing outside the standard library.
Running it as a script rather than importing it means `__init__.py` never executes, which is why that one rule binds one file rather than the whole package.

**The public namespace is the flat one.** Every cache's update code is written against `dandi_cache.<TAB>`, so what completion lists *is* the API as far as anyone writing a cache is concerned.
The implementation modules are private (`_runner.py`, `_config.py`, and the rest), so they never appear there and nothing has to hide them.
The three archive modules are public, because a cache names them directly, and each declares `__all__` and a `__dir__` that returns it so `dandi_cache.s3.<TAB>` lists its functions rather than `json` and `typing`.

`tests/check_core_imports.py` fails on any of the three.

## Versioning

`[project] version` in `pyproject.toml` is the only place the version is written.
`__version__` resolves from it — from the installed distribution's metadata, or, for the copy vendored into the image and imported straight from `src/`, by reading the `pyproject.toml` shipped beside it.

Bump it in any pull request that changes `src/` or `pyproject.toml`: those are the paths that reach the published image, and a release publishes the version as its own image tag, which a cache may have pinned.
A CI- or documentation-only change needs no bump.
The `Version Check` workflow enforces this, and `CHANGELOG.md` records what changed.

## Documentation

The pages are built with Sphinx and published by Read the Docs from `docs/.readthedocs.yaml`.
`fail_on_warning` is on and Read the Docs builds every pull request, so a broken cross-reference fails there rather than after merge.
Build it the same way locally with the command above before pushing.

The worked example is a real cache's own files, under `docs/examples/<name>/`.
The page includes them with `literalinclude` rather than copying them, so it cannot drift from the code it shows, and the test suite parses every `cache.toml` and compiles every `code/` directory there.

## Releasing

Merging to `main` publishes `ghcr.io/dandi-cache/dandi-cache-utils:latest` and `:nwb`, each also tagged with the version and the commit SHA.
A branch build publishes `dev-<branch>` instead, so an environment change can be tried by a cache without overwriting `:latest`.
The monthly scheduled build picks up newer releases of the NWB stack and the DANDI client on a predictable cadence.
