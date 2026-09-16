# Changelog

## Upcoming

### 🚀 Enhancement

- `s3` gained `dandiset_ids`, `asset_manifest_keys`, `dandiset_assets` and `content_id_from_content_urls`, the four things every cache that reads the archive's manifests had its own copy of.
  `asset_manifest_keys` walks every version rather than only `draft`, because an asset a draft has since dropped is still part of the published version holding it ([#13](https://github.com/dandi-cache/dandi-cache-utils/pull/13)).
- `content_id_from_content_urls` is one function rather than a line each cache repeats, because the rule is not obvious and failing it is silent.
  An HDF5 asset's ID is the last segment of its S3 URL and a Zarr asset's is the second to last, so reading the wrong one labels every Zarr asset with a filename rather than raising ([#13](https://github.com/dandi-cache/dandi-cache-utils/pull/13)).
- `dandiset_assets` raises when a manifest is not a JSON array instead of returning it.
  A caller counting assets would otherwise publish a mapping's key count as an asset count and never notice the archive's layout had changed ([#13](https://github.com/dandi-cache/dandi-cache-utils/pull/13)).

- `nwb.walk_structure` takes a `links` policy, because an HDF5 file with a soft link is two different trees and the `valid-nwb-file-to-*` caches were split between them.
  `LINKS_SKIPPED` walks the hard-link object tree, exactly as `h5py.Group.visititems` does, which is what the published values were computed with; `LINKS_FOLLOWED` walks the hierarchy as named, guarding cycles by object address, which is what the out-degree and cophenetic caches always did.
  Half of the archive's NWB files contain a soft link -- `/acquisition/<series>/imaging_plane` routinely points at `/general/optophysiology` -- so this is the difference between migrating a cache and rewriting tens of thousands of its published numbers ([#12](https://github.com/dandi-cache/dandi-cache-utils/pull/12)).
- `Structure` gained `out_degrees` and `total_cophenetic_index`, the two shape measures that cannot be recovered after the fact.
  Both are properties of the arrangement, and the walk is the only place the arrangement exists: two very differently shaped trees can have identical leaf depths ([#12](https://github.com/dandi-cache/dandi-cache-utils/pull/12)).
- `Structure.leaf_depths` now counts a childless group as a leaf, which it always was.
  The field previously counted datasets only, and every cache that computes a tree-shape index has always counted the empty group, so a cache wired to it as it stood would have published different numbers for any file containing one.
  Nothing read the field yet, so the correction costs nothing ([#12](https://github.com/dandi-cache/dandi-cache-utils/pull/12)).
- `nwb.walk_hdf5_group` and `nwb.walk_zarr_group` summarize an already-open hierarchy, the structural counterpart to `inspect_nwbfile_object`.
  They are what makes the walk testable against a file built to contain the constructs the two policies disagree about, rather than only against the archive ([#12](https://github.com/dandi-cache/dandi-cache-utils/pull/12)).

- `dandi-cache dataset-description` gained `--declared`, the rendering a cache commits beside its `cache.toml`, and `--check`, which fails with a diff when that committed copy is not what the configuration declares.
  A repository can now carry the description it publishes without it becoming a second source of truth ([#11](https://github.com/dandi-cache/dandi-cache-utils/pull/11)).
- Every cache now declares BIDS `1.11.1`, the current release, rather than the version each repository happened to carry.
  A cache can still name its own, but the default moving is what keeps the family on one spec ([#10](https://github.com/dandi-cache/dandi-cache-utils/pull/10)).
- The published `dataset_description.json` now carries what BIDS recommends and not only what it requires.
  `GeneratedBy` names this library, its version and the runtime image, and `SourceDatasets` lists the upstream caches, which `cache.toml` already declares as `[[inputs]]` ([#9](https://github.com/dandi-cache/dandi-cache-utils/pull/9)).
- A cache that names no authors no longer publishes `"Authors": []`.
  The field is optional in BIDS, and an empty list asserts there are none rather than leaving it undeclared ([#9](https://github.com/dandi-cache/dandi-cache-utils/pull/9)).

- `run_incremental_update` takes a `batch` that the cache selected itself, so a `refresh` entry point can re-assess what it already recorded.
  `select_stale` existed for exactly that and had no way to reach the loop, whose frontier drops anything already in the cache ([#6](https://github.com/dandi-cache/dandi-cache-utils/pull/6)).
- `run_incremental_update` calls `on_write` after each write of the cache, checkpoints included, so a cache that keeps side outputs in files of their own can write them at the same moments.
  Checkpointing only the main file would otherwise leave a killed run with one file ahead of the others ([#6](https://github.com/dandi-cache/dandi-cache-utils/pull/6)).
- `nwb.inspect_nwbfile_object` runs the NWB Inspector over an already-open file, so a cache can keep opening and inspecting in separate error logs.
  They fail for unrelated reasons, and `inspect_nwbfile` did both in one call ([#6](https://github.com/dandi-cache/dandi-cache-utils/pull/6)).
- `AssetResolver.dandiset()` is public, so a cache can tell "no such Dandiset" from "no such asset within it".
  Both raise `dandi.exceptions.NotFoundError`, so a caller resolving several paths could only report the Dandiset failure once per path rather than once ([#5](https://github.com/dandi-cache/dandi-cache-utils/pull/5)).
- Added `dandi_cache_utils`, the shared codebase every DANDI cache runs on: the JSONL shapes, the logging and size-capped error logs, the incremental frontier and batch loop with its two explicit failure policies, the standard command line, and the concrete DANDI operations (unsigned S3 client, tokenless asset resolution, remote NWB readers, and the structural walk the `valid-nwb-file-to-*` family shares).
- Added the orchestration script, one for every cache, shipped inside the package as `dandi_cache_utils.pipeline` and driven by the cache's own `cache.toml` rather than by straight-line code per repository.
- The CI a cache calls lives in [`dandi-cache-action`](https://github.com/dandi-cache/dandi-cache-action) rather than here: they are actions, versioned at their own interface, and a reusable workflow cannot be listed on the Marketplace.
  A cache's own `update.yml` is a schedule and one step.
- Added the base images, published as `ghcr.io/dandi-cache/dandi-cache-utils:latest` (core, S3 and the DANDI API) and `:nwb` (plus the remote NWB reading stack).
  Each release also publishes the version as its own tag, so a cache can pin its orchestration to an exact release.
- Added the `dandi-cache` command (`compress`, `config show`, `config shell`, `dataset-description`), built on `rich-click`, the distribution's one required dependency.
  The optional extras stay out of the import graph: `api`, `nwb` and `s3` import theirs inside the functions that use them, so a cache on the `:latest` image never pays for the NWB stack.

### 🏠 Internal

- Publishing the base images now asks every cache to rebuild its own image, through a `repository_dispatch` its build workflow listens for.
  A cache's image is built `FROM` the base and `FROM` resolves at build time, so a release reached ghcr and stopped there: a cache kept whatever base it was last built on until its Dockerfile or its dependencies happened to change.
  The caches are found by having a `cache.toml`, so one joins the fan-out by migrating rather than by being added to a list ([#7](https://github.com/dandi-cache/dandi-cache-utils/pull/7)).

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
