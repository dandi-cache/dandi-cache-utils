# Changelog

The version in `pyproject.toml` is the only place the version is written. It names the published
image, so bump it in any pull request that touches `src/`, `bin/` or `pyproject.toml` -- the tree
vendored into that image -- and add an entry below. A change that is purely CI, documentation or
examples needs neither.

## Upcoming

### 🚀 Enhancement

- Added `dandi_cache_utils`, the shared codebase every DANDI cache runs on: the JSONL shapes, the
  logging and size-capped error logs, the incremental frontier and batch loop with its two
  explicit failure policies, the standard command line, and the concrete DANDI operations
  (unsigned S3 client, tokenless asset resolution, remote NWB readers, and the structural walk the
  `valid-nwb-file-to-*` family shares).
- Added `bin/update_pipeline.sh`, one orchestrator for every cache, driven by the cache's own
  `cache.toml` rather than by straight-line code per repository.
- Added the reusable `cache-update.yml` and `cache-image.yml` workflows, which reduce a cache's own
  `update.yml` to a schedule plus three lines.
- Added the base images, published as `ghcr.io/dandi-cache/dandi-cache-utils:latest` (core, S3 and
  the DANDI API) and `:nwb` (plus the remote NWB reading stack). Each release also publishes the
  version as its own tag, so a cache can pin its orchestration to an exact release.
- Added the `dandi-cache` command (`compress`, `config show`, `config shell`,
  `dataset-description`), built on `rich-click`. The library never imports it, so importing
  `dandi_cache_utils` still pulls in nothing outside the standard library.

### 🏠 Internal

- `__version__` resolves from `pyproject.toml` -- from the installed distribution's metadata, or,
  for the copy vendored into the image and imported straight from `src/`, by reading the
  `pyproject.toml` shipped beside it. It is no longer a second copy of the version that can drift.
- The pipeline renders `cache.toml` by running `config.py` directly rather than through
  `python -m`, which drops a `runpy` warning and keeps the bootstrap independent of the command
  line. From the runner's virtual environment onwards it calls the installed `dandi-cache`.
- The package declares its own completion surface. `dandi_cache.<TAB>` lists the API and the three
  accessor modules (`nwb`, `s3`, `api`) rather than the implementation modules bound as a side
  effect of the re-exports, and each accessor module lists what it defines rather than what it
  imports. `tests/check_core_imports.py` checks this alongside the standard-library-only rule.
