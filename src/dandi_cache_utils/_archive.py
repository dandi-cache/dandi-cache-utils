"""Which archive the S3 helpers and the API client read from.

The archives themselves, and what each one's bucket, API and organization are, are declared in
`_config`, beside the `cache.archive` key that names one. This holds which one is selected, which
opening a cache's dataset sets from its `cache.toml`, so a cache's own code never names a bucket.
Without a dataset open, as in a notebook, it is DANDI.
"""

from ._config import ARCHIVES, DEFAULT_ARCHIVE, Archive, archive

_current = ARCHIVES[DEFAULT_ARCHIVE]


def select(name: str, /) -> Archive:
    """Make `name` the archive the S3 helpers and the API client read from."""
    global _current
    _current = archive(name)
    return _current


def current() -> Archive:
    """The archive the S3 helpers and the API client read from."""
    return _current
