# dandi-cache-utils

A centralized codebase of operations used across the [DANDI Cache](https://github.com/dandi-cache).

**[Documentation](https://dandi-cache-utils.readthedocs.io/)**

Every cache in the organization is the same pipeline around a different operation: read one or more upstream caches, work out what is not yet recorded, do something per item, and publish JSON Lines with full DataLad provenance.
This repository is everything except that operation — the library, the orchestration, the container and the CI.

It is not published to PyPI.
It is vendored through GHCR container images: each cache's runtime image is built `FROM` the base image published here, so one pinned digest carries both the library the update code imports and the pipeline script CI runs.

A cache repository is then a declaration, an operation, and a schedule:

```toml
# cache.toml
[cache]
name = "valid-nwb-file-to-number-of-groups"

[[inputs]]
name = "content-id-to-valid-nwb-file"
```

```python
# code/update.py
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
    )
```

## Where to go

| | |
|---|---|
| Starting a new cache | [`cache-template`](https://github.com/dandi-cache/cache-template) and its `setup-cache` skill |
| Moving an existing cache onto this | [Migrating an existing cache](https://dandi-cache-utils.readthedocs.io/en/latest/migration/) |
| What goes in `cache.toml` and `update.py` | [Usage](https://dandi-cache-utils.readthedocs.io/en/latest/usage/) |
| Three real caches, rewritten | [Worked examples](https://dandi-cache-utils.readthedocs.io/en/latest/examples/) |
| Why this exists | [What was duplicated](https://dandi-cache-utils.readthedocs.io/en/latest/duplication/) |
| Working on the library itself | [Development](https://dandi-cache-utils.readthedocs.io/en/latest/development/) |
