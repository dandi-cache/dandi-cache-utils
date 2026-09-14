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

#: `pyproject.toml` is the only place the version is written; `__version__` resolves from it.
_DISTRIBUTION_NAME = "dandi-cache-utils"

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


def _read_version() -> str:
    """Resolve the version from the single place it is declared.

    An installed copy carries it in its distribution metadata. The copy vendored into the image is
    also imported straight from `src/` by the runner's bare `python3`, which installs nothing, so
    that one reads `[project] version` out of the `pyproject.toml` shipped beside the sources.
    Either way the answer comes from `pyproject.toml`, which is the only place it is written.
    """
    import importlib.metadata

    try:
        return importlib.metadata.version(_DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        import pathlib
        import tomllib

        pyproject_path = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"
        if not pyproject_path.is_file():
            raise
        return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]["version"]


def __getattr__(name: str):
    """Expose `dandi_cache_utils.nwb` / `.s3` / `.api`, and `__version__`, on first use.

    The submodules are imported on demand so that the core -- which the pipeline parses with the
    CI runner's bare `python3` -- never pulls in boto3, h5py or pynwb just to read `cache.toml`.
    `__version__` is resolved on demand for the same reason: reading it costs a metadata lookup
    that the orchestration's hot path has no use for.

    Imports are local here rather than at the top of the module precisely because the point is to
    keep them out of the core import.
    """
    if name in _LAZY_SUBMODULES:
        import importlib

        module = importlib.import_module(f".dandi.{name}", __name__)
        globals()[name] = module
        return module
    if name == "__version__":
        version = _read_version()
        globals()["__version__"] = version
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
    return sorted([*__all__, *_LAZY_SUBMODULES])
