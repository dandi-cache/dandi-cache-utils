# dandi-cache-utils

A centralized codebase of operations used across the DANDI Cache.

Every cache in the [dandi-cache](https://github.com/dandi-cache) organization is the same pipeline
around a different operation: read one or more upstream caches, work out what is not yet recorded,
do something per item, and publish JSON Lines with full DataLad provenance. This repository is
everything except that operation — the library, the orchestration, the container, and the CI.

It is not published to PyPI. It is **vendored through GHCR container images**: each cache's runtime
image is built `FROM` the base image published here, so one pinned digest carries the library the
update code imports *and* the pipeline script CI runs.



## Why

The caches were generated from a common template and then diverged. The same fix had to be written
repeatedly, in different ways, and reached a different subset of repositories each time:

| Fix | template | qualifying-aind | content-id-to-nwb-file | content-id-to-usage-dandiset-path | content-id-to-valid-nwb-file | valid-nwb-file-to-number-of-groups |
|---|---|---|---|---|---|---|
| Retry a transient push rejection | yes | yes, rewritten | no | no | no | no |
| Retry a transient `docker pull` | yes | no | no | no | no | yes, rewritten |
| Pre-run dataset cleanliness check | yes | **missing** | yes, rewritten | yes, rewritten | yes, rewritten | yes |
| Save the advanced input pointer with `-d .` | yes | no | yes | yes | yes | yes |
| Keep each run's log on `derivatives` | yes | yes | no | no | yes | yes |

Four repositories fixed "the input subdataset pointer is silently not saved" in four separate pull
requests. Three fixed the cleanliness check that misfires on a clean dataset, in two different
ways. The push retry exists in two incompatible implementations and is absent from three caches.
None of that is per-cache behaviour; it is one pipeline maintained five times.

The Python drifted the same way. A loader that reads a JSONL file into a mapping appears four
times under three names — twice in a single file. Structured logging, the peak-memory line and the
per-item progress log exist in two of six caches, so the other four give no indication of where a
killed run got to. There are four incompatible spellings of the batch-size flag, and in one repo
the workflow's default and the script's default disagree.

See [`docs/duplication.md`](docs/duplication.md) for the full analysis.



## What a cache keeps

After adopting this, a cache repository contains its schedule, its dependencies, and its operation.
Nothing else.

```
cache.toml                                    # inputs, outputs, entry points, batch size, BIDS metadata
code/update.py                                # the operation, and only the operation
containers/Dockerfile                         # FROM the base image, plus this cache's dependencies
.github/workflows/update.yml                  # the schedule; delegates to the shared workflow
.github/workflows/build_and_upload_docker_image.yml
```

Gone from every cache: `code/update_pipeline.sh` (~200 lines), `code/compress.py`, the ~130-line
update workflow, the ~60-line build workflow, `envs/pyproject.toml`, `dataset_description.json`,
and the boilerplate half of `code/update.py`.

[`templates/`](templates) is the starting point for a new cache; [`examples/`](examples) holds real
caches rewritten on this library.



## Usage

### `cache.toml`

One declarative file, read by both the orchestration and the update code, so they cannot disagree:

```toml
[cache]
name = "valid-nwb-file-to-number-of-groups"

[[inputs]]
name = "content-id-to-valid-nwb-file"

[operations.update]
limit = 500

[description]
authors = ["Cody Baker"]
```

Only `cache.name` is required; the file stem, the image reference, the output file name, and each
input's URL, path, branch and file name are all derived from the organization's conventions.

### `code/update.py`

```python
import dandi_cache_utils as dandi_cache


def count_groups(content_id, item):
    item.stage = "reading the NWB file"
    return dandi_cache.nwb.walk_structure(content_id).number_of_groups


def main():
    dataset, arguments = dandi_cache.open_dataset()
    validity = dataset.read_input()

    dandi_cache.run_incremental_update(
        dataset,
        candidates=[content_id for content_id, valid in validity.items() if valid is True],
        process=count_groups,
        limit=dandi_cache.effective_limit(testing=dataset.testing, limit=arguments.limit),
        on_failure=dandi_cache.SKIP,
        stages={"reading the NWB file": "file_read_errors.txt"},
        checkpoint_every=50,
    )


if __name__ == "__main__":
    main()
```

That is the whole of a cache that was 245 lines. The argument parsing, the logging, the incremental
frontier, the batch cap, the per-item progress and summary lines, the error routing, the output
paths and testing mode all come from the library.

### The workflows

```yaml
jobs:
  Update:
    uses: dandi-cache/dandi-cache-utils/.github/workflows/cache-update.yml@main
    with:
      testing: ${{ inputs.testing || false }}
      limit: ${{ inputs.limit || '' }}
    secrets: inherit
```

### The container

```dockerfile
FROM ghcr.io/dandi-cache/dandi-cache-utils:latest
RUN pip install <this cache's dependencies>
```

Use the `:nwb` tag instead for a cache that streams remote NWB files; it already carries h5py,
zarr, pynwb, hdmf-zarr, remfile, s3fs and the NWB Inspector, so those caches need no `pip install`
line at all.



## How the vendoring works

1. This repository publishes `ghcr.io/dandi-cache/dandi-cache-utils` in two flavours, `:latest` and
   `:nwb`. Each carries the installed `dandi_cache_utils` library and, at
   `/opt/dandi-cache-utils/`, the pipeline script, its sources, and the runner's pinned
   requirements.
2. A cache's image is built `FROM` one of those, so it inherits all of it.
3. The shared update workflow pulls the cache's image, extracts `/opt/dandi-cache-utils` from it,
   and runs the pipeline script from there.

The image digest therefore pins the orchestration and the runtime environment together, and that
digest is what each run records in its provenance — so a recorded run can be reproduced from the
digest alone, which was not true when the script lived on the code branch and the image held only
the environment.

`dandi_cache_utils.config` imports nothing outside the standard library, and that is enforced by a
test: the pipeline parses `cache.toml` with the CI runner's bare `python3`, before any environment
has been built.



## The library

| Module | What it replaces |
|---|---|
| `config` | The input URLs, paths, branches, output names, entry points and batch sizes hard-coded in each `update_pipeline.sh`, plus `dataset_description.json` |
| `dataset` | The `sourcedata`/`derivatives`/`logs` path construction and the testing-mode file switching repeated in each `update.py` |
| `jsonl` | Four copies of the mapping loader, three of the writer, and six copies of `compress.py` |
| `logs` | The logging setup and peak-memory helper present in two of six caches, and three incompatible error-log mechanisms |
| `runner` | The incremental frontier, the batch cap, the per-item loop, and the two failure policies |
| `cli` | Four different spellings of `--testing` / `--limit` |
| `dandi.s3` | The unsigned client, the tolerant reads, the content-addressed key layout, the concurrent map |
| `dandi.api` | The tokenless client and the three-line asset resolution, written out in three caches |
| `dandi.nwb` | The HDF5-versus-Zarr dispatch, the streaming readers, and the structural walk shared by the whole `valid-nwb-file-to-*` family |



## Development

```bash
uv run --with ".[test]" pytest
shellcheck bin/update_pipeline.sh
```

A change here reaches every cache in the organization at once. That is the point, and it is also
the risk, so the tests cover the behaviours that differed between repositories before: batch
ordering, what a failure means, and whether testing mode can touch the real cache.
