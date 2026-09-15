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

from . import api, nwb, pipeline, s3
from ._arguments import build_parser, open_dataset, parse_arguments
from ._cli import dandi_cache_cli
from ._config import (
    CONFIG_FILE_NAME,
    CONFIG_PATH_VARIABLE,
    CacheConfig,
    InputCache,
    Operation,
    as_shell,
    load_config,
    parse_config,
    read_config,
)
from ._dataset import TESTING_LIMIT, CacheDataset
from ._jsonl import (
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
from ._logs import ErrorLog, StagedErrorLog, configure_logging, logger, peak_memory_mib
from ._runner import (
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
from ._version import __version__

__all__ = [
    "BatchResult",
    "CONFIG_FILE_NAME",
    "CONFIG_PATH_VARIABLE",
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
    "api",
    "as_shell",
    "build_parser",
    "compress",
    "compress_derivatives",
    "configure_logging",
    "dandi_cache_cli",
    "effective_limit",
    "load_config",
    "logger",
    "nwb",
    "open_dataset",
    "parse_arguments",
    "parse_config",
    "peak_memory_mib",
    "pipeline",
    "read_config",
    "read_ids",
    "read_input",
    "read_lookup",
    "read_records",
    "run_full_rebuild",
    "run_incremental_update",
    "s3",
    "select_new",
    "select_stale",
    "write_ids",
    "write_lookup",
    "write_records",
]
