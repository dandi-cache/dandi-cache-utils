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
| `authors` | omitted | `Authors` |
| `license` | `CC-BY-4.0` | `License` |
| `bids_version` | `1.11.1` | `BIDSVersion` |
| `keywords` | omitted | `Keywords` |
| `references` | the cache's repository URL | `ReferencesAndLinks` |

`DatasetType` is always `study`, because every cache is one, and it is one of the three values BIDS allows alongside `raw` and `derivative`.

Two more fields are rendered from what the cache already declares elsewhere, rather than from this section.
`SourceDatasets` lists the `[[inputs]]`, since a cache's upstream caches are exactly the datasets it was derived from.
`GeneratedBy` names this library, the version doing the generating, and the runtime image, because it describes the process that produced the copy in hand rather than anything the cache declares about itself.

Only `Name` and `BIDSVersion` are required by BIDS; everything else here is what it recommends.
The rendered document is validated against `bidsschematools`, the BIDS maintainers' machine-readable specification, on every test run.

The pipeline writes the file onto the `derivatives` dataset at the start of every run and copies it to `dist`, so it cannot fall behind the declaration.
To see what a cache will publish:

```bash
dandi-cache dataset-description cache.toml
```

### The copy a repository commits

A cache also commits a `dataset_description.json` beside its `cache.toml`, so that what it publishes is readable without checking out a published branch.
That copy is generated, never hand-edited:

```bash
dandi-cache dataset-description --declared --output dataset_description.json
```

`--declared` differs from the published rendering by one key: it records no `GeneratedBy` version.
A version belongs to the run that produced a published copy, not to the repository, and a version baked into a committed file would go stale on this library's next release -- turning every cache red on a release rather than on a mistake.

The build workflow holds the committed copy to the declaration it came from:

```bash
dandi-cache dataset-description --check
```

It fails with a unified diff when the two disagree, and passes when a cache commits no copy at all, since adopting the file is what makes it checked.

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

### Re-assessing what is already recorded

A cache whose answer depends on an evolving external tool has to revisit what it already recorded, which the ordinary frontier never will.
Select that batch with {func}`~dandi_cache_utils.select_stale` and pass it as `batch` rather than `candidates`:

```python
dandi_cache.run_incremental_update(
    dataset,
    batch=dandi_cache.select_stale(recorded, checked_at, limit=limit, fraction_per_run=1 / 30),
    process=inspect,
    recorded=recorded,
)
```

`fraction_per_run` sizes the batch from the cache's own size, so a daily run cycles through all of it over a month however large it grows.

A cache that keeps side outputs about each item, such as when it was last checked, writes them from `on_write`.
That runs after every write of the cache itself, checkpoints included, so all of its files land together and a run killed mid-batch leaves them consistent rather than one file ahead of the others.

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

`FROM` resolves when the cache's image is built, so a new release of this library reaches a cache only once that image is rebuilt.
Publishing the base images asks every cache to do exactly that, through a `repository_dispatch` its build workflow listens for:

```yaml
on:
  repository_dispatch:
    types: [ base-image-published ]
```

A cache is found by having a `cache.toml`, so nothing needs adding to a list when one is migrated.
A cache pinned to a version tag rebuilds on that same pinned base, which is what pinning is for.

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
