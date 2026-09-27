"""The on-disk layout of a cache, and the reads and writes that go with it.

Every cache is the same BIDS-ish study dataset: upstream caches are mounted read-only under
`sourcedata/`, results are written to `derivatives/`, and logs land in `logs/`. The pipeline
mounts that dataset at `/tmp` inside the container and passes it as `--base-directory`, so all
paths are resolved from one place and nothing is hard-coded per cache.

Testing mode redirects every output to a `testing_`-prefixed file name. The real cache is
therefore untouched by a smoke run, and because `cache.toml` declares exactly which files are
published, a testing artifact can never reach the `dist` branch even if one is left behind.
"""

import dataclasses
import pathlib

from . import _jsonl
from ._config import DEFAULT_OPERATION, CacheConfig, InputCache, load_config
from ._logs import LOG_DIRECTORY_NAME, configure_logging

TESTING_FILE_PREFIX = "testing_"


@dataclasses.dataclass(frozen=True)
class CacheDataset:
    """The directories and files of one cache, rooted at `base_directory`."""

    config: CacheConfig
    base_directory: pathlib.Path
    testing: bool = False
    #: Which declared operation this run is, which is what `limit` is resolved against.
    operation: str = DEFAULT_OPERATION

    @classmethod
    def open(
        cls,
        base_directory: pathlib.Path | str,
        /,
        *,
        testing: bool = False,
        config: CacheConfig | None = None,
        operation: str = DEFAULT_OPERATION,
    ) -> "CacheDataset":
        """Open the dataset at `base_directory`, loading `cache.toml` if one was not supplied."""
        return cls(
            config=config if config is not None else load_config(),
            base_directory=pathlib.Path(base_directory),
            testing=testing,
            operation=operation,
        )

    def limit(self, override: int | None = None, /) -> int | None:
        """How many items this run should work through.

        Every cache meters itself: `[operations.<name>] limit` in `cache.toml` is how much of the
        backlog one scheduled run gets through, and running often enough is what clears it. This
        resolves that declaration against the run in hand, so an entry point asks one question
        rather than assembling the answer from three places::

            limit = dataset.limit(arguments.limit)

        The limit bounds *work*, never output. A run that is capped still publishes the complete
        cache: it simply advances the frontier by that much and leaves the rest for the next run.
        Truncating what is published is a different operation, and not one any cache wants.

        Under `--testing` an operation's `testing_limit` wins over everything, because a smoke run
        is meant to be small and fast whatever the cache's ordinary batch size is. Both numbers
        come from `cache.toml` and nowhere else: an operation that declares no `testing_limit`
        simply runs its ordinary batch, and `--testing` then changes only where it writes.
        """
        declared = self.config.operation(self.operation)
        if self.testing and declared.testing_limit is not None:
            return declared.testing_limit
        return override if override is not None else declared.limit

    @property
    def derivatives_directory(self) -> pathlib.Path:
        """Where this cache's own results are written."""
        return self.base_directory / "derivatives"

    @property
    def sourcedata_directory(self) -> pathlib.Path:
        """Where the upstream input caches are mounted as subdatasets."""
        return self.base_directory / "sourcedata"

    @property
    def logs_directory(self) -> pathlib.Path:
        """Where the per-run log and the accumulating error logs are written."""
        return self.base_directory / LOG_DIRECTORY_NAME

    @property
    def log_prefix(self) -> str:
        """Prefix applied to error-log file names, keeping testing failures separate."""
        return TESTING_FILE_PREFIX if self.testing else ""

    def start_logging(self, *, operation: str = "update") -> pathlib.Path:
        """Configure logging for this run and return the log file it will be written to."""
        return configure_logging(self.logs_directory, stem="testing" if self.testing else operation)

    def input_file_path(self, name: str | None = None, /) -> pathlib.Path:
        """The path of an upstream cache's published JSONL file inside `sourcedata/`."""
        input_cache = self.config.only_input if name is None else self.config.input(name)
        return self.base_directory / input_cache.relative_file_path

    def read_input(self, name: str | None = None, /, *, required: bool = True) -> dict | list | set:
        """Read an upstream cache in the shape its `cache.toml` entry declares.

        Inputs are required by default: an input subdataset that failed to check out would
        otherwise be read as empty, and the run would happily publish an empty cache over a good one.
        """
        input_cache: InputCache = self.config.only_input if name is None else self.config.input(name)
        return _jsonl.read_input(self.input_file_path(name), format=input_cache.format, required=required)

    def output_file_path(self, name: str | None = None, /) -> pathlib.Path:
        """The path of one of this cache's declared output files, redirected in testing mode."""
        file_name = self.config.cache_file_name if name is None else name
        if file_name not in self.config.outputs:
            declared = ", ".join(self.config.outputs)
            raise KeyError(f"{file_name!r} is not a declared output of {self.config.name} (declared: {declared}).")
        return self.derivatives_directory / f"{self.log_prefix}{file_name}"

    def read_output_lookup(self, name: str | None = None, /) -> dict:
        """Read this cache's own prior output as a `{key: value}` mapping.

        This is the basis of every incremental update: what is already recorded here is what the
        run does not need to do again.
        """
        return _jsonl.read_lookup(self.output_file_path(name))

    def write_output_lookup(self, records: dict, name: str | None = None, /) -> pathlib.Path:
        """Write a `{key: value}` mapping to one of this cache's declared outputs."""
        file_path = self.output_file_path(name)
        _jsonl.write_lookup(file_path, records)
        return file_path

    def write_output_records(self, records: list, name: str | None = None, /) -> pathlib.Path:
        """Write a list of records, one JSON value per line, to one of this cache's outputs."""
        file_path = self.output_file_path(name)
        _jsonl.write_records(file_path, records)
        return file_path
