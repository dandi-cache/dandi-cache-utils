# Incremental and heavy: `valid-nwb-file-to-number-of-groups`

Counts the internal groups of every valid NWB file. Streaming and walking each file costs real
time, so a run advances the backlog by a bounded batch and leaves a failure for the next one.

**245 lines became 45.** What went: the logging setup, the peak-memory helper, two copies of the
same JSONL loader, the argument parser, the testing-mode file switching, the incremental frontier,
the batch loop with its progress and summary lines, and the S3 layout probe with the HDF5 and Zarr
walks.

## The declaration

```{literalinclude} cache.toml
:language: toml
```

## The operation

```{literalinclude} code/update.py
:language: python
```

## Why these choices

- **`on_failure=dandi_cache.SKIP`.** Every candidate was already opened successfully by the
  upstream cache, so a failure here is almost always transient. Leaving the item unrecorded means a
  later run retries it, rather than a wrong count being written permanently.
- **`limit = 500` in `cache.toml`.** The per-item cost is high enough that a single run cannot
  clear the backlog. The limit lives with the declaration rather than in the workflow, which is how
  one cache came to have a scheduled default of 500 and a dispatch default of 10000.
- **`checkpoint_every=50`.** The original accumulated everything in memory and wrote once at the
  end, so a run killed mid-batch lost all of it. This one keeps what it did.
- **`describe=...`.** Turns each item's log line into `... -> 412 groups`, so a killed run shows
  exactly where it got to.
