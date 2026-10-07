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

#: The files an output declared in `cache.split` is kept as, one per leading hexadecimal digit of its
#: keys. A content ID is a UUID, so its first digit spreads a cache's entries evenly over all sixteen.
SPLIT_PREFIXES = "0123456789abcdef"

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
        """How many items this run should work through, as `cache.toml` declares it::

            limit = dataset.limit(arguments.limit)

        The operation's `testing_limit` under `--testing`, otherwise `override` (an explicit
        `--limit`), otherwise the operation's `limit`. `None` means the run is not capped.
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
        file_path = self.input_file_path(name)
        # An upstream that outgrew one file keeps it split across sixteen. Reading those whenever
        # the single file is absent means a downstream cache needs no change, before or after.
        shard_paths = self._shard_paths(file_path)
        if not file_path.exists() and any(shard_path.exists() for shard_path in shard_paths):
            return _jsonl.read_split_input(shard_paths, format=input_cache.format)
        return _jsonl.read_input(file_path, format=input_cache.format, required=required)

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
        run does not need to do again. A split output is read from its sixteen files, or from the
        single file when there are none yet, which is the whole of its first run's migration.
        """
        file_path = self.output_file_path(name)
        shard_paths = self._shard_paths(file_path)
        if any(shard_path.exists() for shard_path in shard_paths) and (
            file_path.name.removeprefix(self.log_prefix) in self.config.split or not file_path.exists()
        ):
            return _jsonl.read_split_input(shard_paths, format="lookup")
        return _jsonl.read_lookup(file_path)

    def write_output_lookup(self, records: dict, name: str | None = None, /) -> pathlib.Path:
        """Write a `{key: value}` mapping to one of this cache's declared outputs.

        An output declared in `cache.split` is written across its sixteen files instead, and the
        returned path is the one file `dist` publishes it as.
        """
        file_path = self.output_file_path(name)
        if self._is_split(file_path):
            shards: dict[str, dict] = {prefix: {} for prefix in SPLIT_PREFIXES}
            for key in records:
                shards[self._shard_of(key, file_path)][key] = records[key]
            for prefix, shard_path in zip(SPLIT_PREFIXES, self._shard_paths(file_path), strict=True):
                _jsonl.write_lookup(shard_path, shards[prefix])
        else:
            _jsonl.write_lookup(file_path, records)
        self._remove_other_layout(file_path)
        return file_path

    def write_output_records(self, records: list, name: str | None = None, /) -> pathlib.Path:
        """Write a list of records, one JSON value per line, to one of this cache's outputs.

        For an output declared in `cache.split`, each record must be a single-key object, and goes
        to the file its key's first digit names, in the order given.
        """
        file_path = self.output_file_path(name)
        if self._is_split(file_path):
            shards: dict[str, list] = {prefix: [] for prefix in SPLIT_PREFIXES}
            for record in records:
                if not isinstance(record, dict) or len(record) != 1:
                    raise ValueError(f"A split output holds single-key records; {file_path.name} was given {record!r}.")
                shards[self._shard_of(next(iter(record)), file_path)].append(record)
            for prefix, shard_path in zip(SPLIT_PREFIXES, self._shard_paths(file_path), strict=True):
                _jsonl.write_records(shard_path, shards[prefix])
        else:
            _jsonl.write_records(file_path, records)
        self._remove_other_layout(file_path)
        return file_path

    @staticmethod
    def split_output_names(name: str, /) -> list[str]:
        """The sixteen files a split output is kept as: `<stem>_0.jsonl` to `<stem>_f.jsonl`."""
        path = pathlib.PurePosixPath(name)
        return [f"{path.stem}_{prefix}{path.suffix}" for prefix in SPLIT_PREFIXES]

    def _is_split(self, file_path: pathlib.Path, /) -> bool:
        return file_path.name.removeprefix(self.log_prefix) in self.config.split

    def _shard_paths(self, file_path: pathlib.Path, /) -> list[pathlib.Path]:
        return [file_path.with_name(shard) for shard in self.split_output_names(file_path.name)]

    @staticmethod
    def _shard_of(key, file_path: pathlib.Path, /) -> str:
        prefix = str(key)[:1].lower()
        if prefix not in SPLIT_PREFIXES or not prefix:
            raise ValueError(
                f"{key!r} does not start with a hexadecimal digit, so it belongs to none of the files "
                f"{file_path.name} is split across. Only a cache keyed by content ID can be split."
            )
        return prefix

    def _remove_other_layout(self, file_path: pathlib.Path, /) -> None:
        """Remove what the output was kept as before, so a split, or its undoing, leaves one copy."""
        if self._is_split(file_path):
            file_path.unlink(missing_ok=True)
        else:
            for shard_path in self._shard_paths(file_path):
                shard_path.unlink(missing_ok=True)
