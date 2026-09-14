# A full rebuild: `content-id-to-nwb-file`

Keeps the upstream entries whose asset path names an NWB file. No network, no per-item cost, and
nothing to resume — so it recomputes the whole subset each run.

## The declaration

```{literalinclude} ../../examples/content-id-to-nwb-file/cache.toml
:language: toml
```

## The operation

```{literalinclude} ../../examples/content-id-to-nwb-file/code/update.py
:language: python
```

## Why these choices

- **{func}`~dandi_cache_utils.runner.run_full_rebuild`, not an incremental update.** There is
  nothing expensive to avoid repeating, and a filter that recomputes is always consistent with its
  input — including when an upstream record is corrected or withdrawn, which an incremental cache
  would never notice.
- **No `limit` in `cache.toml`.** The batch cap here is a bounded smoke test (`--testing`) rather
  than a backlog policy.
- **`dandi_cache.nwb.is_nwb_path`.** The `.nwb` suffix test lived as an inline expression here, in
  a helper in one sibling, and in a third spelling in another — one of which dropped `.nwb.zarr`
  assets. There is one of it now, and it keeps `.nwb.zarr` everywhere.
