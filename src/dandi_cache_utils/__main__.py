"""The `dandi-cache` command: the few pipeline steps that are pure data handling.

These used to be a `code/compress.py` copied into every cache repository, plus a handful of inline
shell. They are subcommands here so that the orchestration script calls one tool it can rely on
being present in the image.
"""

import argparse
import json
import pathlib
import sys

from . import config as config_module
from . import jsonl


def _compress(arguments: argparse.Namespace) -> int:
    compressed = jsonl.compress_derivatives(arguments.base_directory)
    for file_path in compressed:
        print(file_path)
    if not compressed:
        print(f"No derivatives/*.jsonl files found under {arguments.base_directory}.", file=sys.stderr)
    return 0


def _config(arguments: argparse.Namespace) -> int:
    cache_config = (
        config_module.read_config(arguments.file) if arguments.file is not None else config_module.load_config()
    )
    if arguments.command == "shell":
        print(config_module.as_shell(cache_config, operation=arguments.operation))
    else:
        print(config_module.describe(cache_config))
    return 0


def _dataset_description(arguments: argparse.Namespace) -> int:
    cache_config = (
        config_module.read_config(arguments.file) if arguments.file is not None else config_module.load_config()
    )
    rendered = json.dumps(cache_config.description, indent=4) + "\n"
    if arguments.output is None:
        print(rendered, end="")
        return 0

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered)
    print(arguments.output)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `dandi-cache` console script."""
    parser = argparse.ArgumentParser(prog="dandi-cache", description="Utilities shared by every DANDI cache.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    compress_parser = subparsers.add_parser("compress", help="Gzip derivatives/*.jsonl for distribution.")
    compress_parser.add_argument(
        "--base-directory",
        type=pathlib.Path,
        default=pathlib.Path.cwd(),
        help="The directory containing the `derivatives` directory (the dataset clone in the pipeline).",
    )
    compress_parser.set_defaults(handler=_compress)

    config_parser = subparsers.add_parser("config", help="Inspect a cache.toml.")
    config_parser.add_argument("command", choices=["shell", "show"], help="`shell` emits bash assignments.")
    config_parser.add_argument("file", type=pathlib.Path, nargs="?", default=None, help="Path to cache.toml.")
    config_parser.add_argument("--operation", default=config_module.DEFAULT_OPERATION, help="Operation to describe.")
    config_parser.set_defaults(handler=_config)

    description_parser = subparsers.add_parser(
        "dataset-description",
        help="Render the BIDS dataset_description.json declared in cache.toml.",
    )
    description_parser.add_argument("file", type=pathlib.Path, nargs="?", default=None, help="Path to cache.toml.")
    description_parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=None,
        help="Write to this path instead of standard output.",
    )
    description_parser.set_defaults(handler=_dataset_description)

    arguments = parser.parse_args(argv)
    return arguments.handler(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
