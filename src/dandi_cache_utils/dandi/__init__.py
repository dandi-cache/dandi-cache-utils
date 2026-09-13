"""Concrete operations against the DANDI archive, shared by every cache that touches it.

The submodules are imported on demand rather than here, so that importing one does not require
the dependencies of the others: a cache that only reads S3 need not install `pynwb`.

- `s3`  reads the public archive bucket directly, keyed by content ID.
- `api` resolves Dandiset paths to streamable URLs through the REST API.
- `nwb` streams remote NWB files and walks their internal structure.
"""

__all__ = ["api", "nwb", "s3"]


def __getattr__(name: str):
    if name in __all__:
        import importlib

        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
