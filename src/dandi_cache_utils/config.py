"""The declarative description of a cache, read from its `cache.toml`.

This is the single source of truth that both halves of the pipeline read: the Python update
logic imports `load_config`, and the pipeline script evaluates the output of
`python3 config.py cache.toml` to learn the same facts. Everything a cache repository used to
spell out twice -- once in `update_pipeline.sh` and once in `code/update.py` -- is declared here
exactly once.

This module deliberately imports nothing outside the standard library. The pipeline script
parses `cache.toml` with the CI runner's bare `python3`, before any environment is built, so
adding a third-party import here would break the orchestration.
"""

import dataclasses
import os
import pathlib
import shlex
import sys
import tomllib
import typing

CONFIG_FILE_NAME = "cache.toml"

# Every cache in the organization lives here and publishes its runtime image here, so both are
# derived from the cache name rather than repeated in each `cache.toml`.
ORGANIZATION = "dandi-cache"
REPOSITORY_URL_TEMPLATE = "https://github.com/{organization}/{name}.git"
IMAGE_TEMPLATE = "ghcr.io/{organization}/{name}"

# Caches publish their data on a dedicated branch; their default branch holds only code.
DEFAULT_INPUT_BRANCH = "derivatives"

# The three shapes an upstream cache's JSON Lines file can take, as consumed by
# `dandi_cache_utils.jsonl.read_input`:
#   lookup   one single-key object per line, merged into one `{key: value}` mapping
#   records  one independent JSON value per line, kept as a list
#   ids      one bare scalar per line, collected into a set
INPUT_FORMATS = ("lookup", "records", "ids")

DEFAULT_OPERATION = "update"


def underscored(name: str, /) -> str:
    """Convert a hyphenated cache name to the underscored form used for file names."""
    return name.replace("-", "_")


@dataclasses.dataclass(frozen=True)
class InputCache:
    """An upstream cache registered as a DataLad input subdataset of this one."""

    name: str
    url: str
    path: str
    branch: str
    file_name: str
    format: str

    @property
    def relative_file_path(self) -> pathlib.PurePosixPath:
        """The input file's path relative to the cache's base directory."""
        return pathlib.PurePosixPath(self.path) / "derivatives" / self.file_name


@dataclasses.dataclass(frozen=True)
class Operation:
    """One entry point of a cache: the ordinary `update`, plus any extras such as `refresh`."""

    name: str
    script: str
    label: str
    limit: int | None


@dataclasses.dataclass(frozen=True)
class CacheConfig:
    """The full declarative description of one cache repository."""

    name: str
    file_stem: str
    image: str
    outputs: tuple[str, ...]
    inputs: tuple[InputCache, ...]
    operations: dict[str, Operation]
    #: The BIDS study description, published with the dataset on both `derivatives` and `dist`.
    description: dict = dataclasses.field(default_factory=dict)
    #: The repository root, when the config was read from a file; used as the default base directory.
    directory: pathlib.Path | None = None

    @property
    def cache_file_name(self) -> str:
        """The primary derivatives file name, e.g. `my_cache.jsonl`."""
        return f"{self.file_stem}.jsonl"

    def operation(self, name: str, /) -> Operation:
        """Look up one operation by name, with a helpful error listing the declared ones."""
        if name not in self.operations:
            declared = ", ".join(sorted(self.operations)) or "none"
            raise KeyError(f"No operation named {name!r} in {CONFIG_FILE_NAME} (declared: {declared}).")
        return self.operations[name]

    def input(self, name: str, /) -> InputCache:
        """Look up one input cache by its repository name."""
        for input_cache in self.inputs:
            if input_cache.name == name:
                return input_cache
        declared = ", ".join(input_cache.name for input_cache in self.inputs) or "none"
        raise KeyError(f"No input named {name!r} in {CONFIG_FILE_NAME} (declared: {declared}).")

    @property
    def only_input(self) -> InputCache:
        """The single input cache, for the common one-input case."""
        if len(self.inputs) != 1:
            raise ValueError(f"{self.name} declares {len(self.inputs)} inputs; name the one you want with `input()`.")
        return self.inputs[0]


def _require_mapping(value: typing.Any, /, *, where: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError(f"`{where}` in {CONFIG_FILE_NAME} must be a table, got {type(value).__name__}.")
    return value


def _parse_input(raw: dict, /) -> InputCache:
    name = raw.get("name")
    if not name:
        raise ValueError(f"Every `[[inputs]]` entry in {CONFIG_FILE_NAME} requires a `name`.")

    input_format = raw.get("format", "lookup")
    if input_format not in INPUT_FORMATS:
        allowed = ", ".join(INPUT_FORMATS)
        raise ValueError(f"Input {name!r} has format {input_format!r}; expected one of: {allowed}.")

    return InputCache(
        name=name,
        url=raw.get("url", REPOSITORY_URL_TEMPLATE.format(organization=ORGANIZATION, name=name)),
        path=raw.get("path", f"sourcedata/{name}"),
        branch=raw.get("branch", DEFAULT_INPUT_BRANCH),
        file_name=raw.get("file", f"{underscored(name)}.jsonl"),
        format=input_format,
    )


def _parse_operations(raw: dict, /, *, cache_name: str) -> dict[str, Operation]:
    operations = {}
    for name, entry in raw.items():
        entry = _require_mapping(entry, where=f"operations.{name}")
        limit = entry.get("limit")
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ValueError(f"Operation {name!r} has limit {limit!r}; expected a positive integer or no limit.")
        operations[name] = Operation(
            name=name,
            script=entry.get("script", f"code/{name}.py"),
            label=entry.get("label", name.capitalize()),
            limit=limit,
        )

    # Every cache can always be updated, even when it declares no `[operations]` table at all.
    if DEFAULT_OPERATION not in operations:
        operations[DEFAULT_OPERATION] = Operation(
            name=DEFAULT_OPERATION,
            script=f"code/{DEFAULT_OPERATION}.py",
            label=DEFAULT_OPERATION.capitalize(),
            limit=None,
        )

    for operation in operations.values():
        if operation.script.startswith("/") or ".." in pathlib.PurePosixPath(operation.script).parts:
            raise ValueError(f"Operation {operation.name!r} of {cache_name} must name a path inside the repository.")
    return operations


DEFAULT_BIDS_VERSION = "1.10.0"
DEFAULT_LICENSE = "CC-BY-4.0"


def _parse_description(raw: dict, /, *, name: str) -> dict:
    """Build the BIDS `dataset_description.json` contents from the `[description]` table.

    Generating it from `cache.toml` is what lets a cache repository drop the hand-maintained file
    that only ever differed from its neighbours by one `Name` field -- and it removes a whole class
    of drift, such as one cache quietly declaring a different BIDS version and no license.
    """
    description = {
        "Name": raw.get("title", name),
        "BIDSVersion": raw.get("bids_version", DEFAULT_BIDS_VERSION),
        "DatasetType": "study",
        "License": raw.get("license", DEFAULT_LICENSE),
        "Authors": list(raw.get("authors", [])),
    }
    if raw.get("keywords"):
        description["Keywords"] = list(raw["keywords"])
    description["ReferencesAndLinks"] = list(
        raw.get("references", [f"https://github.com/{ORGANIZATION}/{name}"]),
    )
    return description


def parse_config(raw: dict, /, *, directory: pathlib.Path | None = None) -> CacheConfig:
    """Build a `CacheConfig` from an already-parsed `cache.toml` mapping."""
    cache = _require_mapping(raw.get("cache", {}), where="cache")

    name = cache.get("name")
    if not name:
        raise ValueError(f"{CONFIG_FILE_NAME} requires `cache.name` (the hyphenated repository name).")
    if name != name.lower() or "_" in name or " " in name:
        raise ValueError(f"`cache.name` must be the lowercase, hyphenated repository name; got {name!r}.")

    file_stem = cache.get("file_stem", underscored(name))
    outputs = tuple(cache.get("outputs", [f"{file_stem}.jsonl"]))
    if not outputs:
        raise ValueError(f"{name} declares no `cache.outputs`; at least one derivatives file must be published.")
    for output in outputs:
        if not output.endswith(".jsonl"):
            raise ValueError(f"`cache.outputs` entry {output!r} must be a `.jsonl` file name.")

    inputs = tuple(_parse_input(entry) for entry in raw.get("inputs", []))
    input_paths = [input_cache.path for input_cache in inputs]
    if len(set(input_paths)) != len(input_paths):
        raise ValueError(f"{name} declares two inputs at the same `path`; each needs its own `sourcedata/` location.")

    return CacheConfig(
        name=name,
        file_stem=file_stem,
        image=cache.get("image", IMAGE_TEMPLATE.format(organization=ORGANIZATION, name=name)),
        outputs=outputs,
        inputs=inputs,
        operations=_parse_operations(_require_mapping(raw.get("operations", {}), where="operations"), cache_name=name),
        description=_parse_description(_require_mapping(raw.get("description", {}), where="description"), name=name),
        directory=directory,
    )


#: Set by the pipeline so the container never has to guess where the repository was mounted.
CONFIG_PATH_VARIABLE = "DANDI_CACHE_CONFIG"


def find_config(start: pathlib.Path | str | None = None, /) -> pathlib.Path:
    """Locate a cache's `cache.toml`.

    The search order is what makes one library work in all three places a cache runs: the pipeline
    container (where the repository is bind-mounted at an arbitrary path and the variable is set),
    a developer's checkout, and the tests.

    1. `$DANDI_CACHE_CONFIG`, if set.
    2. Upward from `start`, when given.
    3. Upward from the directory of the running entry point (`code/update.py` -> the repository root).
    4. Upward from the current working directory.
    """
    from_environment = os.environ.get(CONFIG_PATH_VARIABLE)
    if from_environment:
        candidate = pathlib.Path(from_environment)
        if not candidate.is_file():
            raise FileNotFoundError(f"{CONFIG_PATH_VARIABLE} points at {candidate}, which is not a file.")
        return candidate

    searched = []
    starting_points = [pathlib.Path(start)] if start is not None else []
    if not starting_points:
        script = pathlib.Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else None
        starting_points = [point for point in (script, pathlib.Path.cwd()) if point is not None]

    for starting_point in starting_points:
        starting_point = starting_point.resolve()
        searched.append(str(starting_point))
        for candidate_directory in (starting_point, *starting_point.parents):
            candidate = candidate_directory / CONFIG_FILE_NAME
            if candidate.is_file():
                return candidate

    raise FileNotFoundError(
        f"No {CONFIG_FILE_NAME} found in {' or '.join(searched)} or any parent directory. "
        f"Set {CONFIG_PATH_VARIABLE} to point at it explicitly."
    )


def load_config(start: pathlib.Path | str | None = None, /) -> CacheConfig:
    """Find and read a cache's `cache.toml`; see `find_config` for the search order."""
    return read_config(find_config(start))


def read_config(file_path: pathlib.Path | str, /) -> CacheConfig:
    """Read and validate one `cache.toml` file."""
    file_path = pathlib.Path(file_path)
    with file_path.open(mode="rb") as file_stream:
        raw = tomllib.load(file_stream)
    try:
        return parse_config(raw, directory=file_path.resolve().parent)
    except (ValueError, TypeError) as exception:
        raise type(exception)(f"{file_path}: {exception}") from exception


def _shell_array(name: str, values: typing.Iterable[str], /) -> str:
    quoted = " ".join(shlex.quote(value) for value in values)
    return f"{name}=({quoted})"


def as_shell(config: CacheConfig, /, *, operation: str = DEFAULT_OPERATION) -> str:
    """Render the config as bash assignments for `update_pipeline.sh` to `eval`.

    Emitting the arrays from here -- rather than hand-maintaining them in each cache's shell
    script -- is what lets one orchestrator serve a cache with no inputs and a cache with three.
    """
    selected = config.operation(operation)
    lines = [
        f"CACHE_NAME={shlex.quote(config.name)}",
        f"CACHE_FILE_STEM={shlex.quote(config.file_stem)}",
        f"CACHE_IMAGE={shlex.quote(config.image)}",
        _shell_array("CACHE_OUTPUTS", config.outputs),
        _shell_array("INPUT_PATHS", [input_cache.path for input_cache in config.inputs]),
        _shell_array("INPUT_URLS", [input_cache.url for input_cache in config.inputs]),
        _shell_array("INPUT_BRANCHES", [input_cache.branch for input_cache in config.inputs]),
        f"OPERATION_NAME={shlex.quote(selected.name)}",
        f"OPERATION_SCRIPT={shlex.quote(selected.script)}",
        f"OPERATION_LABEL={shlex.quote(selected.label)}",
        f"OPERATION_DEFAULT_LIMIT={shlex.quote('' if selected.limit is None else str(selected.limit))}",
    ]
    return "\n".join(lines)


def describe(config: CacheConfig, /) -> str:
    """A human-readable summary of a cache's configuration."""
    lines = [
        f"cache:      {config.name}",
        f"image:      {config.image}",
        f"outputs:    {', '.join(config.outputs)}",
        f"operations: {', '.join(sorted(config.operations))}",
    ]
    lines.extend(
        f"input:      {entry.name} ({entry.format}) @ {entry.branch} -> {entry.path}" for entry in config.inputs
    )
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    """The bootstrap entry point, run as a plain script by the pipeline.

    This is deliberately not part of the `dandi-cache` command: the pipeline renders the config
    before the runner has an environment, so this path must work with a bare `python3` and the
    vendored sources alone. `dandi-cache config shell` prints the same thing, once there is an
    environment to run it in.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Render a DANDI cache's cache.toml as bash assignments.")
    parser.add_argument("config", type=pathlib.Path, nargs="?", default=None, help=f"Path to {CONFIG_FILE_NAME}.")
    parser.add_argument("--operation", default=DEFAULT_OPERATION, help="Which operation to render.")
    arguments = parser.parse_args(argv)

    config = read_config(arguments.config) if arguments.config is not None else load_config()
    print(as_shell(config, operation=arguments.operation))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
