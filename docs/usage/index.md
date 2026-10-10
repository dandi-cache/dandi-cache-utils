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
testing_limit = 2

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
| `split` | `[]` | The outputs kept on `derivatives` as sixteen files rather than one. See below. |
| `split_files` | `16` | How many files each split output is kept as: 16, by the first digit of each key, or 256, by the first two. |
| `publish_split` | `false` | Publish the split outputs to `dist` as their separate compressed files rather than joined into one. |

#### Outputs near GitHub's file limit

GitHub refuses a push carrying any file over 100 MiB, and the pipeline pushes `derivatives` only once a run's work is done, so an output that outgrows the limit costs every run all of its work.
Two things guard against it.

- **A size check before every push.** The pipeline runs `dandi-cache check-sizes` on what it is about to push to `derivatives`, and again on what `dist` is about to publish.
  A file past 80% of the limit is a warning: an annotation on the run, a line in its summary, and a `size-warnings` (or `dist-size-warnings`) step output that `dandi-cache-action` sends an email for, while the run still succeeds.
  A file past the limit stops the run before the push, naming the file and what to do.
- **`split`.** An output listed there is kept on `derivatives` as `<stem>_0.jsonl` to `<stem>_f.jsonl`, by the first hexadecimal digit of each key, so only a cache keyed by content ID can split.
  Nothing in `code/update.py` changes: `read_output_lookup`, `write_output_lookup` and `write_output_records` read and write the sixteen files for a split output, and the first run after declaring it reads the single file and removes it.
  `dist` still publishes it as the one `<stem>.jsonl.gz` it always did, the sixteen joined in order, so no consumer URL changes; compressed, it is a fraction of the size.
  A downstream cache needs no change either: `read_input` reads an upstream's sixteen files whenever its single file is absent.

An output too large for sixteen files of under 100 MiB sets `split_files = 256`, and one too large to publish as one file even compressed sets `publish_split = true`, which puts its 256 files on `dist` one by one.
Changing either is a one-line change whose next run migrates the files.

```toml
[cache]
name = "content-id-to-dandiset-paths"
outputs = ["content_id_to_dandiset_paths.jsonl", "asset_manifest_checked_at.jsonl"]
# Within days of GitHub's 100 MiB limit as one file.
split = ["content_id_to_dandiset_paths.jsonl"]
```

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

`update` always exists and defaults to `code/update.py`; the example above declares its limits.
Declare another only when a cache genuinely has one — such as a `refresh` that re-assesses what it already recorded:

```toml
[operations.refresh]
label = "Refresh"
```

| Key | Default | Meaning |
|---|---|---|
| `script` | `code/<name>.py` | The entry point this operation runs. |
| `label` | the capitalized `name` | How the operation is named in logs and commit messages. |
| `limit` | none | How many items one run works through. |
| `testing_limit` | none | What `--testing` uses instead. Must not exceed `limit`. Without one, `--testing` runs the ordinary batch and changes only where it writes. |

#### What a limit means

**A limit bounds the work a run does, never the records it publishes.**

Every cache is metered: `limit` is how much of the backlog one cron gets through, and running often enough is what clears it.
A capped run still publishes the complete cache — it advances the frontier by that much and leaves the rest for the next run.

This is why {func}`~dandi_cache_utils.run_full_rebuild` takes no limit.
Capping the records on the way out would publish a truncated file, which does not defer the rest of the cache but deletes it from every consumer.
A cache with no frontier still meters itself, by bounding what it *fetches* inside `build`:

```python
# Bound the work: how many Dandisets this run reads.
dandiset_ids = dandi_cache.select_new(all_dandiset_ids, recorded, limit=dataset.limit(arguments.limit))
...
# Publish everything known, including what earlier runs resolved.
dandi_cache.run_full_rebuild(dataset, build=build)
```

A cache that genuinely has nothing to meter — a filter or a join over inputs already in hand, which completes in full every run — declares no `limit` and says so in a comment.
For those, `--testing` changes only where the output is written.

`dataset.limit(arguments.limit)` resolves the cap for the run in hand, and is the only thing that does: the operation's `testing_limit` under `--testing`, otherwise an explicit `--limit`, otherwise the declared `limit`.

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
        limit=dataset.limit(arguments.limit),
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
- **`dandi_cache.RETRY`** writes `failure_value` as well, so the failure and its reason are published, and selects the item again on a later run.
  `retry_when` says which recorded values are worth another attempt, and those come after every item never tried.
  Right when a consumer needs to see what failed and the failure may still clear — a walk that timed out.

All three are correct for different caches and choosing wrongly is a real bug, so the shared runner asks for the choice rather than picking one quietly.

`failure_value` may be a callable.
It is called with the item and its error scope, and `scope.exception` is what was raised, so the recorded value can say why.

### Bounding one item's time

A file that takes far longer than its neighbours holds up the whole batch, and a stalled read inside `h5py` cannot be interrupted safely in the process that made it.
{func}`~dandi_cache_utils.run_isolated` runs one call in a forked child process and kills it past `timeout_seconds`, raising `TimeoutError`:

```python
def measure(content_id, item):
    item.stage = "reading the NWB file"
    return dandi_cache.run_isolated(walk, arguments=(content_id,), timeout_seconds=20 * 60)
```

The call's own exception is raised as it was, and a child that dies without answering raises `ChildProcessError`.
The result crosses back by pickling, so it should be plain data.

### Bounding a batch's memory

A child process is also the only way to give memory back.
HDF5, pynwb and the NWB Inspector hold on to what they allocate, so one process that opens and inspects files grows with every file, about 5 MB each, and never shrinks: a batch of 2500 reached 10 GB.
A runner has 16 GB, and a process that outgrows it is not stopped politely.
The run stalls for most of an hour and is then killed, and because a killed run publishes nothing, the results it had already checkpointed are lost with it.

So a cache that opens files itself should do it in a child per file, as above, returning only plain data, and the growth stays in the children.
Fork the child (the default) after importing `h5py`, `pynwb` and the rest in the parent, so it inherits them instead of importing them again for every file.
A `cache.toml` `limit` is then about time, not memory.

`run_incremental_update` also watches the process's peak memory.
Once it passes `memory_limit_mib`, which defaults to three quarters of the memory the machine or its container has, the batch stops early and writes what it has, and `BatchResult.stopped_for_memory` says so.
That turns a lost batch into a short one, but the growth is still the cache's to fix; the log line says how far the batch got.
Pass `memory_limit_mib=math.inf` to turn it off.

### Choosing the link policy

The other thing the library will not guess, for a cache that walks an NWB file's structure.

An HDF5 file with a soft link is two different trees, and half of the archive's NWB files have one: `/acquisition/<series>/imaging_plane` routinely points at the plane's real home under `/general/optophysiology`.
Whether that plane's datasets are children of the series or only of `general` is not a detail — it changes the leaf depths, the dataset count, and every index computed from them.

- **`dandi_cache.nwb.LINKS_SKIPPED`** (the default) walks the hard-link object tree: a soft or external link is not a child, and an object reachable twice is counted once, at the first path that reaches it.
  This is exactly what `h5py.Group.visititems` does, so it is what a cache that predates this library has already published.
- **`dandi_cache.nwb.LINKS_FOLLOWED`** walks the hierarchy as it is named: every entry of a group is a child of it, whatever kind of link put it there.
  A group already walked is not walked again — that is what stops a link cycle — but it is still a child of the node that named it.

Neither is the right one.
A cache migrating onto the library picks the one its published values were computed with, and `Structure.links` records the choice.

Zarr has no links, so the policy does not apply to it and `Structure.links` is `None`.

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

A cache that is a derivation of its inputs rather than an accumulation of per-item work -- a filter, a join, a reshaping -- uses {func}`~dandi_cache_utils.run_full_rebuild` instead.
It takes the same `dataset`, builds the whole mapping in one pass, and has no frontier or failure policy to choose.

It takes no `limit`, deliberately: the `[operations]` section above covers why, and how a rebuild cache meters what it fetches instead.

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
      - uses: dandi-cache/dandi-cache-action@v2
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
dandi-cache check-sizes .                   # files near GitHub's 100 MiB limit
```

Against a cache repository with no local environment:

```bash
docker run --rm -v "$PWD":/w:ro ghcr.io/dandi-cache/dandi-cache-utils:latest \
  dandi-cache config show /w/cache.toml
```
