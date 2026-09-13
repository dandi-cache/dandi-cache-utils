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

__version__ = "0.1.0"

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
    "__version__",
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
    """Expose `dandi_cache_utils.nwb` / `.s3` / `.api` lazily.

    They are imported on first use so that the core -- which the pipeline parses with the CI
    runner's bare `python3` -- never pulls in boto3, h5py or pynwb just to read `cache.toml`.
    """
    if name in ("api", "nwb", "s3"):
        import importlib

        module = importlib.import_module(f".dandi.{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
