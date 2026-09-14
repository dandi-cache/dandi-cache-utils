"""Shared operations, orchestration, and CI for every DANDI cache.

Every cache in the organization is the same pipeline around a different operation: read one or
more upstream caches, work out what is not yet recorded, do something per item, and publish JSON
Lines with full provenance. This package is everything except that operation.

A cache's `code/update.py` is then only the part that is actually unique to it::

    import dandi_cache_utils as dandi_cache

    def count(content_id):
        number_of_groups, description = dandi_cache.nwb.count_groups(content_id)
        return number_of_groups

    if __name__ == "__main__":
        dataset, arguments = dandi_cache.open_dataset()
        validity = dataset.read_input()
        dandi_cache.run_incremental_update(
            dataset,
            candidates=[content_id for content_id, valid in validity.items() if valid is True],
            process=count,
            limit=dandi_cache.effective_limit(testing=dataset.testing, limit=arguments.limit),
        )

Everything else -- the logging, the batch selection, the failure policy, the output paths, the
testing mode, the shell orchestration, the container, and the CI workflows -- comes from here.
"""

from .cli import build_parser, open_dataset, parse_arguments
from .config import CacheConfig, InputCache, Operation, load_config, read_config
from .dataset import TESTING_LIMIT, CacheDataset
from .jsonl import (
    compress,
    compress_derivatives,
    read_ids,
    read_input,
    read_lookup,
    read_records,
    write_ids,
    write_lookup,
    write_records,
)
from .logs import ErrorLog, StagedErrorLog, configure_logging, logger, peak_memory_mib
from .runner import (
    NOTHING,
    RECORD,
    SKIP,
    BatchResult,
    effective_limit,
    run_full_rebuild,
    run_incremental_update,
    select_new,
    select_stale,
)

#: The modules a cache reaches for by name. They are bound on first use rather than imported
#: here, so a cache that only reads S3 never pays for `pynwb`. They are deliberately absent from
#: `__all__`: `from dandi_cache_utils import *` would otherwise drag in h5py and boto3.
_LAZY_SUBMODULES = ("api", "nwb", "s3")

#: Bound on first use for the same reason: `__version__` costs a metadata lookup the pipeline's
#: hot path has no use for, and the command line needs rich-click, which the library must not.
_LAZY_ATTRIBUTES = ("__version__", "dandi_cache_cli")

__all__ = [
    "BatchResult",
    "CacheConfig",
    "CacheDataset",
    "ErrorLog",
    "InputCache",
    "NOTHING",
    "Operation",
    "RECORD",
    "SKIP",
    "StagedErrorLog",
    "TESTING_LIMIT",
    "build_parser",
    "compress",
    "compress_derivatives",
    "configure_logging",
    "effective_limit",
    "load_config",
    "logger",
    "open_dataset",
    "parse_arguments",
    "peak_memory_mib",
    "read_config",
    "read_ids",
    "read_input",
    "read_lookup",
    "read_records",
    "run_full_rebuild",
    "run_incremental_update",
    "select_new",
    "select_stale",
    "write_ids",
    "write_lookup",
    "write_records",
]


def __getattr__(name: str):
    """Bind `nwb` / `s3` / `api`, `dandi_cache_cli` and `__version__` on first use.

    Everything here is imported on demand so that the core -- which the pipeline parses with the
    CI runner's bare `python3` -- never pulls in boto3, h5py, pynwb or rich-click just to read
    `cache.toml`. The imports are local for exactly that reason.
    """
    if name in _LAZY_SUBMODULES:
        import importlib

        module = importlib.import_module(f".dandi.{name}", __name__)
        globals()[name] = module
        return module
    if name == "dandi_cache_cli":
        from ._cli import dandi_cache_cli

        globals()[name] = dandi_cache_cli
        return dandi_cache_cli
    if name == "__version__":
        from ._version import read_version

        version = read_version()
        globals()[name] = version
        return version
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """The public surface, which is what an editor or a shell offers on `dandi_cache.<TAB>`.

    Without this, completion gets the namespace backwards: it lists the implementation modules
    (`cli`, `config`, `dataset`, `jsonl`, `logs`, `runner`, `dandi`), which are bound as
    attributes only as a side effect of the re-exports above and whose contents are all
    re-exported anyway, while hiding `nwb`, `s3` and `api`, which are the modules a cache actually
    reaches for and are bound only once used.

    Hiding them from completion does not unimport them: `import dandi_cache_utils.config` still
    works, and is how the pipeline reads `cache.toml`.
    """
    return sorted({*__all__, *_LAZY_SUBMODULES, *_LAZY_ATTRIBUTES})
