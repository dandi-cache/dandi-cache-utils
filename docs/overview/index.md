# How it fits together

## What a cache keeps

After adopting this library, a cache repository contains its schedule, its dependencies and its
operation. Nothing else.

```text
cache.toml                                    # inputs, outputs, entry points, batch size, metadata
code/update.py                                # the operation, and only the operation
containers/Dockerfile                         # FROM the base image, plus this cache's dependencies
envs/pyproject.toml                           # this cache's own processing dependencies
.github/workflows/update.yml                  # the schedule; delegates to the shared workflow
.github/workflows/build_and_upload_docker_image.yml
```

Gone from every cache: `code/update_pipeline.sh` (~200 lines), `code/compress.py`, the ~130-line
update workflow, the ~60-line build workflow, `dataset_description.json`, and the boilerplate half
of `code/update.py`.

## The three branches

Each cache is one repository with three branches, and that has not changed:

- **`main`** holds the declaration, the operation, the container definition and the two workflows.
- **`derivatives`** is a persistent [DataLad](https://www.datalad.org/) dataset on its own branch.
  Each update is recorded there with `datalad containers-run`, so every revision carries the exact
  command, the input subdataset commits, the output diff, the runtime image digest, and the run's
  own log under `logs/`.
- **`dist`** is the lightweight, force-recreated publication artifact that downstream users read.
  Only the outputs `cache.toml` declares are published to it.

## How the vendoring works

1. This repository publishes `ghcr.io/dandi-cache/dandi-cache-utils` in two flavours, `:latest`
   and `:nwb`, plus a tag for each release. Every image carries the installed `dandi_cache_utils`
   library and, at `/opt/dandi-cache-utils/`, the pipeline script, its sources, and the runner's
   pinned requirements.
2. A cache's own image is built `FROM` one of those, so it inherits all of it.
3. The shared update workflow pulls the cache's image, extracts `/opt/dandi-cache-utils` from it,
   and runs the pipeline script from there.

The image digest therefore pins the orchestration *and* the runtime environment together, and that
digest is what each run records in its provenance — so a recorded run can be reproduced from the
digest alone. That was not true when the script lived on the code branch and the image held only
the environment.

:::{note}
`dandi_cache_utils.config` imports nothing outside the standard library, and a test enforces it.
The pipeline's first step parses `cache.toml` with the CI runner's bare `python3`, before any
environment has been built, by running `config.py` directly. Everything after that runs through the
installed `dandi-cache` command.
:::

## What the library replaces

| Module | What it replaces |
|---|---|
| {mod}`dandi_cache_utils.config` | The input URLs, paths, branches, output names, entry points and batch sizes hard-coded in each `update_pipeline.sh`, plus `dataset_description.json` |
| {mod}`dandi_cache_utils.dataset` | The `sourcedata`/`derivatives`/`logs` path construction and the testing-mode file switching repeated in each `update.py` |
| {mod}`dandi_cache_utils.jsonl` | Four copies of the mapping loader, three of the writer, and six copies of `compress.py` |
| {mod}`dandi_cache_utils.logs` | The logging setup and peak-memory helper present in two of six caches, and three incompatible error-log mechanisms |
| {mod}`dandi_cache_utils.runner` | The incremental frontier, the batch cap, the per-item loop, and the two failure policies |
| {mod}`dandi_cache_utils.cli` | Four different spellings of `--testing` / `--limit` |
| {mod}`dandi_cache_utils.dandi.s3` | The unsigned client, the tolerant reads, the content-addressed key layout, the concurrent map |
| {mod}`dandi_cache_utils.dandi.api` | The tokenless client and the three-line asset resolution, written out in three caches |
| {mod}`dandi_cache_utils.dandi.nwb` | The HDF5-versus-Zarr dispatch, the streaming readers, and the structural walk shared by the whole `valid-nwb-file-to-*` family |

[What was duplicated](../duplication/index.md) has the evidence behind that table.
