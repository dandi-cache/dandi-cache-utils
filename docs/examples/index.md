# A worked example

`valid-nwb-file-to-number-of-groups` counts the internal groups of every valid NWB file.
Streaming and walking each file costs real time, so a run advances the backlog by a bounded batch and leaves a failure for the next one.

It is not a sketch.
These two files are copies of that repository's own, and the test suite parses the `cache.toml` and compiles the code on every run, so an API change that would break it fails here rather than in a cache's next scheduled update.

**245 lines became 43.** What went: the logging setup, the peak-memory helper, two copies of the same JSONL loader, the argument parser, the testing-mode file switching, the incremental frontier, the batch loop with its progress and summary lines, and the S3 layout probe with the HDF5 and Zarr walks.

## The declaration

```{literalinclude} valid-nwb-file-to-number-of-groups/cache.toml
:language: toml
```

## The operation

```{literalinclude} valid-nwb-file-to-number-of-groups/code/update.py
:language: python
```

## Why these choices

- **`on_failure=dandi_cache.SKIP`.** Every candidate was already opened successfully by the upstream cache, so a failure here is almost always transient.
  Leaving the item unrecorded means a later run retries it, rather than a wrong count being written permanently.
- **`limit = 500` in `cache.toml`.** The per-item cost is high enough that a single run cannot clear the backlog.
  The limit lives with the declaration rather than in the workflow, which is how one cache came to have a scheduled default of 500 and a dispatch default of 10000.
- **`checkpoint_every=50`.** The original accumulated everything in memory and wrote once at the end, so a run killed mid-batch lost all of it.
  This one keeps what it did.
- **`describe=...`.** Turns each item's log line into `... -> 412 groups`, so a killed run shows exactly where it got to.

## The other shapes

This cache is incremental with one output, which is the common case.
A cache that is a pure filter, with no per-item work worth resuming, calls {func}`~dandi_cache_utils.run_full_rebuild` instead.
A cache that writes several files in one pass declares them all under `cache.outputs`, and one that has a second entry point declares it as another `[operations.<name>]` table.
Both are covered in the [usage reference](../usage/index.md).
