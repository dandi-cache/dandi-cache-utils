# dandi-cache-utils

A centralized codebase of operations used across the [DANDI Cache](https://github.com/dandi-cache).

Every cache in the organization is the same pipeline around a different operation: read one or
more upstream caches, work out what is not yet recorded, do something per item, and publish JSON
Lines with full DataLad provenance. This project is everything except that operation — the
library, the orchestration, the container and the CI — so a cache repository holds only what makes
it different from its siblings.

It is not published to PyPI. It is vendored through GHCR container images: each cache's runtime
image is built `FROM` the base image published here, so one pinned digest carries both the library
the update code imports and the pipeline script CI runs.

```{toctree}
:maxdepth: 2
:caption: Contents
```
