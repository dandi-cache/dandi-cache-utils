# Usage

A cache declares itself in `cache.toml`, writes its operation in `code/update.py`, and delegates everything else.
This page is the reference for those pieces.
[A worked example](../examples/index.md) shows them in full.

## `cache.toml`

One declarative file, read by both the orchestration and the update code, so the two cannot disagree:

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

Only `cache.name` is required.
The file stem, the image reference, the output file name, and each input's URL, path, branch and file name are all derived from the organization's conventions.

### `[cache]`

| Key | Default | Meaning |
|---|---|---|
| `name` | *required* | The hyphenated repository name. |
| `file_stem` | the underscored `name` | The stem of the default output file. |
| `image` | `ghcr.io/dandi-cache/<name>` | This cache's runtime image. |
| `outputs` | `["<file_stem>.jsonl"]` | Every file published to `dist`. Declaring them is what keeps a `testing_` artifact from reaching consumers. |

### `[[inputs]]`

One entry per upstream cache.
Each is registered as a DataLad subdataset and pinned with `--input` in every run's provenance.

| Key | Default | Meaning |
|---|---|---|
| `name` | *required* | The upstream cache's repository name. |
| `url` | `https://github.com/dandi-cache/<name>.git` | Where to clone it from. |
| `path` | `sourcedata/<name>` | Where it is registered in the dataset. |
| `branch` | `derivatives` | The branch the upstream publishes on. |
| `file_name` | `<underscored name>.jsonl` | The file to read within it. |
| `format` | `lookup` | How {meth}`~dandi_cache_utils.CacheDataset.read_input` parses it. |

The three formats are the three shapes a cache's JSON Lines file takes: `lookup` (one single-key object per line, merged into one mapping), `records` (one independent JSON value per line, kept as a list) and `ids` (one bare scalar per line, collected into a set).

Omit the section entirely for a first-in-chain cache that fetches its own inputs over the network.
Nothing is then pinned, and the container must be able to reach the upstream source at run time.

### `[operations]`

`update` always exists and defaults to `code/update.py`.
Declare another only when a cache genuinely has one — such as a `refresh` that re-assesses what it already recorded.

```toml
[operations.update]
limit = 500        # the default batch size for a complete run

[operations.refresh]
label = "Refresh"  # `script` defaults to code/refresh.py
```

Set `limit` only for a cache too heavy to clear its backlog in one run; most caches should be incremental and leave it unset.

### `[description]`

Rendered to `dataset_description.json` on the published branches, so no cache carries that file.

| Key | Default | Becomes |
|---|---|---|
| `title` | the cache name | `Name` |
| `authors` | empty | `Authors` |
| `license` | `CC-BY-4.0` | `License` |
| `bids_version` | `1.10.0` | `BIDSVersion` |
| `keywords` | omitted | `Keywords` |
| `references` | the cache's repository URL | `ReferencesAndLinks` |

`DatasetType` is always `study`, because every cache is one.

The pipeline writes the file onto the `derivatives` dataset at the start of every run and copies it to `dist`, so it cannot fall behind the declaration.
To see what a cache will publish:

```bash
dandi-cache dataset-description cache.toml
```

## `code/update.py`

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

That is the whole of a cache that was 245 lines.
The argument parsing, the logging, the incremental frontier, the batch cap, the per-item progress and summary lines, the error routing, the output paths and testing mode all come from the library.

### Choosing the failure policy

The one thing {func}`~dandi_cache_utils.run_incremental_update` will not guess:

- **`dandi_cache.SKIP`** leaves a failed item unrecorded, so a later run retries it.
  Right when the work is known to be possible and a failure is almost always transient — a network read.
- **`dandi_cache.RECORD`** writes `failure_value`, so the item is never retried.
  Right when the failure *is* the answer — a file that does not validate.

Both are correct for different caches and choosing wrongly is a real bug, so the shared runner asks for the choice rather than picking one quietly.

### Rebuilding instead of resuming

A cache that is a pure filter or reshaping of its input, with no per-item work worth resuming, uses {func}`~dandi_cache_utils.run_full_rebuild` instead.
It takes the same `dataset`, builds the whole mapping in one pass, and has no frontier, limit or failure policy to choose.

## The workflows

```yaml
jobs:
  Update:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: read
    timeout-minutes: 330
    steps:
      - uses: dandi-cache/dandi-cache-action@v1
        with:
          token: ${{ secrets._GITHUB_API_KEY }}
          testing: ${{ inputs.testing || false }}
          limit: ${{ inputs.limit || '' }}
          mail-username: ${{ secrets.MAIL_USERNAME }}
          mail-password: ${{ secrets.MAIL_PASSWORD }}
```

A cache with a second entry point adds a job passing `operation: refresh`.
What stays in the cache's own workflow is the schedule, the concurrency group and the dispatch inputs.

## The container

```dockerfile
FROM ghcr.io/dandi-cache/dandi-cache-utils:latest
COPY envs/pyproject.toml /tmp/build/pyproject.toml
RUN pip install /tmp/build && rm -rf /tmp/build
```

Use the `:nwb` tag instead for a cache that streams remote NWB files; it already carries h5py, zarr, pynwb, hdmf-zarr, remfile, s3fs and the NWB Inspector, so those caches often need no dependencies of their own at all.
Pin a release tag (`:0.1.0`) instead of `:latest` to hold a cache's orchestration still.

## The `dandi-cache` command

The image also carries a command for the pipeline steps that are pure data handling.
It is rarely run by hand, but it is how a cache's configuration can be inspected:

```bash
dandi-cache config show cache.toml          # what the declaration resolves to
dandi-cache config shell cache.toml         # the same, as bash assignments
dandi-cache dataset-description cache.toml  # the BIDS metadata, rendered
dandi-cache compress                        # gzip derivatives/*.jsonl reproducibly
```

Against a cache repository with no local environment:

```bash
docker run --rm -v "$PWD":/w:ro ghcr.io/dandi-cache/dandi-cache-utils:latest \
  dandi-cache config show /w/cache.toml
```
