# Changelog

The version in `pyproject.toml` is the only place the version is written.
It names the published image, so bump it in any pull request that touches `src/` or `pyproject.toml` -- the tree vendored into that image -- and add an entry below.

## Upcoming

### 🚀 Enhancement

- Added `dandi_cache_utils`, the shared codebase every DANDI cache runs on: the JSONL shapes, the logging and size-capped error logs, the incremental frontier and batch loop with its two explicit failure policies, the standard command line, and the concrete DANDI operations (unsigned S3 client, tokenless asset resolution, remote NWB readers, and the structural walk the `valid-nwb-file-to-*` family shares).
- Added the orchestration script, one for every cache, shipped inside the package as `dandi_cache_utils.pipeline` and driven by the cache's own `cache.toml` rather than by straight-line code per repository.
- The CI a cache calls lives in [`dandi-cache-action`](https://github.com/dandi-cache/dandi-cache-action) rather than here: they are actions, versioned at their own interface, and a reusable workflow cannot be listed on the Marketplace.
  A cache's own `update.yml` is a schedule and one step.
- Added the base images, published as `ghcr.io/dandi-cache/dandi-cache-utils:latest` (core, S3 and the DANDI API) and `:nwb` (plus the remote NWB reading stack).
  Each release also publishes the version as its own tag, so a cache can pin its orchestration to an exact release.
- Added the `dandi-cache` command (`compress`, `config show`, `config shell`, `dataset-description`), built on `rich-click`, the distribution's one required dependency.
  The optional extras stay out of the import graph: `api`, `nwb` and `s3` import theirs inside the functions that use them, so a cache on the `:latest` image never pays for the NWB stack.

### 🏠 Internal

- The image build now runs the test suite against the library as installed in the image, in both the `:latest` and `:nwb` stages.
  The test matrix only ever exercised the sources on the runner, where the extras the package must not import are not installed, so `check_core_imports.py` was passing partly by luck; inside the image they are present.

- Adopted the organization's `AGENTS.md` conventions.
  Every test carries the `ai_generated` marker, the tests import only what `__init__.py` exposes publicly (`dandi_cache_cli` among it, bound lazily so the library still imports without rich-click), and version resolution moved out of `__init__.py` into `_version.py`.

- `__version__` resolves from `pyproject.toml` -- from the installed distribution's metadata, or, for the copy vendored into the image and imported straight from `src/`, by reading the `pyproject.toml` shipped beside it.
  It is no longer a second copy of the version that can drift.
- The pipeline renders `cache.toml` by running `config.py` directly rather than through `python -m`, which drops a `runpy` warning and keeps the bootstrap independent of the command line.
  From the runner's virtual environment onwards it calls the installed `dandi-cache`.
- Every module declares `__all__` and a `__dir__` that returns it, not just the package and the three DANDI accessors.
  `dandi_cache.jsonl.<TAB>` listed `gzip`, `json`, `pathlib`, `shutil` and `typing` alongside its functions; it lists its functions now.
- The package declares its own completion surface.
  `dandi_cache.<TAB>` lists the API and the three accessor modules (`nwb`, `s3`, `api`) rather than the implementation modules bound as a side effect of the re-exports, and each accessor module lists what it defines rather than what it imports.
  `tests/check_core_imports.py` checks this alongside the standard-library-only rule.
