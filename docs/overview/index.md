# How it fits together

## What a cache keeps

After adopting this library, a cache repository contains its schedule, its dependencies and its operation.
Nothing else.

```text
cache.toml                                    # inputs, outputs, entry points, batch size, metadata
code/update.py                                # the operation, and only the operation
containers/Dockerfile                         # FROM the base image, plus this cache's dependencies
envs/pyproject.toml                           # this cache's own processing dependencies
.github/workflows/update.yml                  # the schedule; delegates to the shared workflow
.github/workflows/build_and_upload_docker_image.yml
```

Gone from every cache: `code/update_pipeline.sh` (~200 lines), `code/compress.py`, the ~130-line update workflow, the ~60-line build workflow, `dataset_description.json`, and the boilerplate half of `code/update.py`.

## What lives where

Three repositories serve every cache, and the line between them is whether a file is *referenced* at run time or *copied once* at generation time.

| | [`dandi-cache-utils`](https://github.com/dandi-cache/dandi-cache-utils) | [`dandi-cache-action`](https://github.com/dandi-cache/dandi-cache-action) | [`cache-template`](https://github.com/dandi-cache/cache-template) |
|---|---|---|---|
| **Holds** | The library, the orchestration script it ships as `dandi_cache_utils.pipeline`, and the base container image | The two actions a cache's CI calls: the update and the image build | The skeleton of a cache: `cache.toml`, `code/update.py`, `containers/Dockerfile`, the calling workflows, the README, and the setup skills |
| **Reaches a cache by** | Being referenced — `FROM` for the image, which carries the library and the script | Being referenced — `uses:` at the tag the cache pins | Being copied, when the repository is generated from it |
| **A change to it** | Takes effect on the next image build a cache picks up | Takes effect on every cache's next run, at the pinned tag | Affects only caches generated afterwards |

A template's files are copied once and then diverge — which is the problem this library exists to end, and precisely what happened to the ~130-line update workflow each cache used to carry.
What is referenced instead is fixed once for everyone.

The actions and the pipeline they run are split across two repositories on purpose.
The action is CI glue with an interface of its own — its inputs — and it is versioned at that interface, so a cache pins `@v1` there while tracking the runtime image separately.
The orchestration script is not CI glue: it is the thing being run, and it ships inside the image so that the digest recorded in a run's provenance pins the pipeline and the runtime environment together.
The action's job is to pull that image, extract the script, and run it.

What the template holds is the *caller*: a cache's own `update.yml` is a schedule, a concurrency group, dispatch inputs and one step.
That is genuinely per-cache, so it is copied and then owned.

## The three branches

Each cache is one repository with three branches, and that has not changed:

- **`main`** holds the declaration, the operation, the container definition and the two workflows.
- **`derivatives`** is a persistent [DataLad](https://www.datalad.org/) dataset on its own branch.
  Each update is recorded there with `datalad containers-run`, so every revision carries the exact command, the input subdataset commits, the output diff, the runtime image digest, and the run's own log under `logs/`.
- **`dist`** is the lightweight, force-recreated publication artifact that downstream users read.
  Only the outputs `cache.toml` declares are published to it.

## How the vendoring works

1. This repository publishes `ghcr.io/dandi-cache/dandi-cache-utils` in two flavours, `:latest` and `:nwb`, plus a tag for each release.
   Every image carries the installed `dandi_cache_utils` library and, at `/opt/dandi-cache-utils/`, the pipeline script, its sources, and the runner's pinned requirements.
2. A cache's own image is built `FROM` one of those, so it inherits all of it.
3. The shared update workflow pulls the cache's image, extracts `/opt/dandi-cache-utils` from it, and runs the pipeline script from there.

The image digest therefore pins the orchestration *and* the runtime environment together, and that digest is what each run records in its provenance — so a recorded run can be reproduced from the digest alone.
That was not true when the script lived on the code branch and the image held only the environment.

The orchestration script is not a separate tree beside the package: it is part of it, at `dandi_cache_utils/pipeline/update_pipeline.sh`.
It is data the package ships rather than an executable installed onto anyone's PATH, so every copy — a wheel, an editable checkout, the one inside the image — carries it at the same place.
`dandi-cache pipeline --path` says where, and `dandi-cache pipeline` runs it.

:::{note} The module behind `cache.toml` imports nothing outside the standard library, and a test enforces it.
The pipeline's first step parses `cache.toml` with the CI runner's bare `python3`, before any environment has been built, by running `_config.py` as a plain script.
Everything after that runs through the installed `dandi-cache` command. :::

## What the library replaces

| Where it lives now | What it replaces |
|---|---|
| `cache.toml` and its parser | The input URLs, paths, branches, output names, entry points and batch sizes hard-coded in each `update_pipeline.sh`, plus `dataset_description.json` |
| `CacheDataset` | The `sourcedata`/`derivatives`/`logs` path construction and the testing-mode file switching repeated in each `update.py` |
| The JSON Lines readers and writers | Four copies of the mapping loader, three of the writer, and six copies of `compress.py` |
| The logging and error logs | The logging setup and peak-memory helper present in two of six caches, and three incompatible error-log mechanisms |
| `run_incremental_update` and `run_full_rebuild` | The incremental frontier, the batch cap, the per-item loop, and the two failure policies |
| `open_dataset` and the standard flags | Four different spellings of `--testing` / `--limit` |
| {mod}`dandi_cache_utils.s3` | The unsigned client, the tolerant reads, the content-addressed key layout, the concurrent map |
| {mod}`dandi_cache_utils.api` | The tokenless client and the three-line asset resolution, written out in three caches |
| {mod}`dandi_cache_utils.nwb` | The HDF5-versus-Zarr dispatch, the streaming readers, and the structural walk shared by the whole `valid-nwb-file-to-*` family |

[What was duplicated](../duplication/index.md) has the evidence behind that table.
