# Worked examples

Three real caches, rewritten on this library. They are not sketches: each is the repository's own
`code/update.py` after the boilerplate was removed, and the test suite parses their `cache.toml`
and compiles their code on every run, so an API change that would break them fails here rather
than in a cache's next scheduled update.

Between them they cover the three shapes a cache takes:

| Example | Shape |
|---|---|
| [`valid-nwb-file-to-number-of-groups`](valid-nwb-file-to-number-of-groups/index.md) | Incremental, heavy per item, skip on failure, one output |
| [`content-id-to-nwb-file`](content-id-to-nwb-file/index.md) | A cheap filter with nothing to resume, so a full rebuild each run |
| [`content-id-to-valid-nwb-file`](content-id-to-valid-nwb-file/index.md) | Incremental, three parallel outputs, record on failure, plus a second entry point |

```{toctree}
:maxdepth: 1
:hidden:

valid-nwb-file-to-number-of-groups/index
content-id-to-nwb-file/index
content-id-to-valid-nwb-file/index
```
