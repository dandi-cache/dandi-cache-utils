"""The `dandi-cache` command line: the pipeline steps that are pure data handling.

These were a `code/compress.py` copied into every cache repository plus a handful of inline shell.
As subcommands they are one tool the orchestration can rely on being present in the image.

The library itself never imports this module, so importing `dandi_cache_utils` still pulls in
nothing outside the standard library -- which matters, because the pipeline script reads
`cache.toml` on the runner before any environment exists. That bootstrap runs
`config.py` directly rather than through this command; everything after it, once the runner's
environment is built, comes through here.
"""

import difflib
import importlib.metadata
import json
import os
import pathlib

import rich_click

from . import _check, _config, _jsonl, pipeline
from ._version import __version__

CONFIG_ARGUMENT = rich_click.argument(
    "file",
    type=rich_click.Path(exists=True, dir_okay=False, path_type=pathlib.Path),
    required=False,
    default=None,
)
OPERATION_OPTION = rich_click.option(
    "--operation",
    default=_config.DEFAULT_OPERATION,
    show_default=True,
    help="Which entry point from the cache's [operations] table to describe.",
)


def _load(file: pathlib.Path | None, /) -> _config.CacheConfig:
    """Read the given `cache.toml`, or find the one belonging to the current cache."""
    return _config.read_config(file) if file is not None else _config.load_config()


@rich_click.group(name="dandi-cache", context_settings={"help_option_names": ["-h", "--help"]})
@rich_click.version_option(version=importlib.metadata.version("dandi-cache-utils"), prog_name="dandi-cache")
def dandi_cache_cli() -> None:
    """Utilities shared by every DANDI cache."""


@dandi_cache_cli.command("compress")
@rich_click.option(
    "--base-directory",
    type=rich_click.Path(file_okay=False, path_type=pathlib.Path),
    default=pathlib.Path.cwd,
    help="The directory holding `derivatives/` (the dataset clone, in the pipeline).",
)
def compress_command(base_directory: pathlib.Path) -> None:
    """Gzip `derivatives/*.jsonl` for publication to the `dist` branch.

    The archives are written reproducibly, so a cache that has not changed republishes
    byte-identical files rather than a new artifact on every run.
    """
    compressed = _jsonl.compress_derivatives(base_directory)
    for file_path in compressed:
        rich_click.echo(file_path)
    if not compressed:
        rich_click.echo(f"No derivatives/*.jsonl files found under {base_directory}.", err=True)


@dandi_cache_cli.group("config")
def config_group() -> None:
    """Inspect a cache's `cache.toml`."""


@config_group.command("show")
@CONFIG_ARGUMENT
def config_show_command(file: pathlib.Path | None) -> None:
    """Print what the configuration resolves to, for a human."""
    rich_click.echo(_config.describe(_load(file)))


@config_group.command("shell")
@CONFIG_ARGUMENT
@OPERATION_OPTION
def config_shell_command(file: pathlib.Path | None, operation: str) -> None:
    """Print the configuration as bash assignments, for `eval`.

    The pipeline does not call this: it runs `config.py` directly, because it parses
    `cache.toml` before any environment exists. This is the same rendering, for looking at what
    the pipeline will see.
    """
    rich_click.echo(_config.as_shell(_load(file), operation=operation))


@dandi_cache_cli.command(
    "pipeline",
    context_settings={"ignore_unknown_options": True},
)
@rich_click.option("--path", "print_path", is_flag=True, help="Print where the script is instead of running it.")
@rich_click.argument("arguments", nargs=-1, type=rich_click.UNPROCESSED)
def pipeline_command(print_path: bool, arguments: tuple[str, ...]) -> None:
    """Run the shared update pipeline, or say where it is.

    The script is part of this package rather than something installed onto the PATH, so this is
    how to reach it without knowing how the installation is laid out. CI extracts the vendored
    copy from the container image and runs it directly; everything else can run it from here.
    """
    if print_path:
        rich_click.echo(pipeline.SCRIPT_PATH)
        return
    os.execvp("bash", ["bash", str(pipeline.SCRIPT_PATH), *arguments])


#: The name of the committed copy, relative to the `cache.toml` it is rendered from.
DESCRIPTION_FILE = "dataset_description.json"


@dandi_cache_cli.command("check-operations")
@CONFIG_ARGUMENT
def check_operations_command(file: pathlib.Path | None) -> None:
    """Check this cache's own operation scripts against the installed library.

    The image build proves the image and the configuration are sound, and says nothing about the
    one file the cache itself contributes. A script naming a function this library does not have
    passes every other check and fails at the next scheduled run, on the real data.

    An import would not catch it: a name used inside a function body is resolved when that function
    runs, so this reads the syntax tree instead.
    """
    config = _load(file)
    problems = _check.check_operations(config)
    if problems:
        rich_click.echo(f"{config.name}: {len(problems)} problem(s) in its operation scripts.", err=True)
        for problem in problems:
            rich_click.echo(f"  {problem}", err=True)
        raise SystemExit(1)

    scripts = ", ".join(operation.script for _name, operation in sorted(config.operations.items()))
    rich_click.echo(f"{scripts}: every `dandi_cache_utils` name used is one this library offers.")


@dandi_cache_cli.command("dataset-description")
@CONFIG_ARGUMENT
@rich_click.option(
    "--output",
    type=rich_click.Path(dir_okay=False, path_type=pathlib.Path),
    default=None,
    help="Write to this path instead of standard output.",
)
@rich_click.option(
    "--declared",
    is_flag=True,
    help="Render the repository's copy, which records no generating version. Implied by --check.",
)
@rich_click.option(
    "--check",
    is_flag=True,
    help=f"Compare `{DESCRIPTION_FILE}` beside the config against --declared and fail if it differs.",
)
def dataset_description_command(
    file: pathlib.Path | None,
    output: pathlib.Path | None,
    declared: bool,
    check: bool,
) -> None:
    """Render the BIDS `dataset_description.json` declared in `cache.toml`.

    Two renderings, differing by one key. The published one, which the pipeline writes onto the
    published branches, records in `GeneratedBy` the library version that produced that copy. The
    declared one does not, because a repository is not a copy anything generated, and a version
    baked into a committed file would go stale the moment the library releases -- which would turn
    every cache red on a release rather than on a mistake.

    `--check` is how CI holds the committed copy to the declaration it came from. A cache that has
    no copy passes: the file is optional, and adopting it is what makes it checked.
    """
    config = _load(file)
    rendered = (
        json.dumps(_config.dataset_description(config, version=None if declared or check else __version__), indent=4)
        + "\n"
    )
    if check:
        return _check_description(config, rendered=rendered)
    if output is None:
        rich_click.echo(rendered, nl=False)
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered)
    rich_click.echo(output)


def _check_description(config: _config.CacheConfig, /, *, rendered: str) -> None:
    """Fail loudly, and with the diff, when the committed copy is not what `cache.toml` declares."""
    committed = (config.directory or pathlib.Path.cwd()) / DESCRIPTION_FILE
    if not committed.is_file():
        rich_click.echo(f"{committed} does not exist, so there is nothing to disagree with the configuration.")
        return

    found = committed.read_text()
    if found == rendered:
        rich_click.echo(f"{committed} is what cache.toml declares.")
        return

    difference = difflib.unified_diff(
        found.splitlines(keepends=True),
        rendered.splitlines(keepends=True),
        fromfile=f"{committed} (committed)",
        tofile="cache.toml (declared)",
    )
    rich_click.echo(f"ERROR: {committed} is not what cache.toml declares.", err=True)
    rich_click.echo("".join(difference), err=True, nl=False)
    rich_click.echo(
        "\nRegenerate it with:\n" f"  dandi-cache dataset-description --declared --output {DESCRIPTION_FILE}",
        err=True,
    )
    raise SystemExit(1)
