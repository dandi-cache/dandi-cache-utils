"""The command line every cache entry point shares.

Each repository grew its own flavour of this: some took `--testing`, some `--limit`, one took
both with mutually exclusive meanings, and the defaults ranged from none to 10000 with the
workflow and the shell script disagreeing about which applied. There is one spelling here:

    --base-directory   where `sourcedata/`, `derivatives/` and `logs/` live
    --testing          process a handful of items and write `testing_`-prefixed outputs
    --limit            cap this run's batch, overriding what `cache.toml` declares

Neither flag is where a cache's batch size is decided. `[operations.<name>] limit` in `cache.toml`
is, and `--limit` only overrides it for one run; `--testing` substitutes that operation's
`testing_limit`, which is the smallest batch that still exercises it. The two compose rather than
conflict: testing always wins on size, so a smoke run is small whatever the cache's ordinary batch
size is.
"""

import argparse
import pathlib
import typing

from ._config import DEFAULT_OPERATION, CacheConfig, load_config
from ._dataset import CacheDataset
from ._logs import logger

BASE_DIRECTORY_HELP = (
    "The directory containing the `sourcedata`, `derivatives` and `logs` directories. Set to the "
    "mounted dataset path when run inside the pipeline container; defaults to the repository root."
)
TESTING_HELP = (
    "Run in testing mode: process the operation's `testing_limit` items, as `cache.toml` declares "
    "it, and write `testing_`-prefixed files instead of the real cache, leaving it untouched. "
    "Omit for an ordinary run."
)
LIMIT_HELP = (
    "Cap the number of items this run works through, overriding the operation's declared `limit`. "
    "The cap bounds the work, not the output: the run still publishes the complete cache and "
    "leaves the rest of the backlog for the next one."
)


def build_parser(
    *,
    description: str,
    testing: bool = True,
    limit: bool = True,
    default_limit: int | None = None,
    default_base_directory: pathlib.Path | None = None,
) -> argparse.ArgumentParser:
    """Build the standard parser, optionally omitting the flags a cache has no use for."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--base-directory",
        type=pathlib.Path,
        default=default_base_directory if default_base_directory is not None else pathlib.Path.cwd(),
        help=BASE_DIRECTORY_HELP,
    )
    if testing:
        parser.add_argument("--testing", action="store_true", help=TESTING_HELP)
    if limit:
        parser.add_argument("--limit", type=int, default=default_limit, help=LIMIT_HELP)
    return parser


def parse_arguments(
    argv: typing.Sequence[str] | None = None,
    *,
    description: str | None = None,
    config: CacheConfig | None = None,
    operation: str = DEFAULT_OPERATION,
    testing: bool = True,
    limit: bool = True,
) -> tuple[argparse.Namespace, CacheConfig]:
    """Parse the standard arguments, defaulting the batch cap from `cache.toml`."""
    config = config if config is not None else load_config()
    declared = config.operation(operation)
    parser = build_parser(
        description=description or f"{declared.label} the {config.name} DANDI cache.",
        testing=testing,
        limit=limit,
        default_limit=declared.limit,
        default_base_directory=config.directory,
    )
    return parser.parse_args(argv), config


def open_dataset(
    argv: typing.Sequence[str] | None = None,
    *,
    description: str | None = None,
    operation: str = DEFAULT_OPERATION,
    testing: bool = True,
    limit: bool = True,
) -> tuple[CacheDataset, argparse.Namespace]:
    """Parse arguments, open the dataset, and start the run log.

    This is the first line of every cache entry point::

        dataset, arguments = dandi_cache_utils.open_dataset()
    """
    arguments, config = parse_arguments(
        argv, description=description, operation=operation, testing=testing, limit=limit
    )
    dataset = CacheDataset.open(
        arguments.base_directory,
        testing=getattr(arguments, "testing", False),
        config=config,
        operation=operation,
    )
    log_file_path = dataset.start_logging(operation=operation)
    logger.info("Logging to %s.", log_file_path)
    logger.info(
        "%s %s (base directory %s%s).",
        config.operation(operation).label,
        config.name,
        dataset.base_directory,
        "; testing mode" if dataset.testing else "",
    )
    return dataset, arguments
