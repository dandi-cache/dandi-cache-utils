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
import typing

from . import _jsonl
from ._config import (
    DEFAULT_OPERATION,
    SPLIT_FILE_COUNTS,
    CacheConfig,
    InputCache,
    load_config,
    split_names,
    split_prefixes,
)
from ._logs import LOG_DIRECTORY_NAME, configure_logging

#: The files an output declared in `cache.split` is kept as by default, one per leading hexadecimal
#: digit of its keys. A content ID is a UUID, so its first digit spreads a cache's entries evenly.
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
        paths = self._input_paths(name)
        if paths is not None:
            return _jsonl.read_split_input(paths, format=input_cache.format)
        return _jsonl.read_input(self.input_file_path(name), format=input_cache.format, required=required)

    def iter_input(self, name: str | None = None, /) -> typing.Iterator[tuple[typing.Any, typing.Any]]:
        """Yield an upstream lookup cache's `(key, value)` entries one at a time, in key order.

        For an input too large to hold in memory whole: only one entry is parsed at a time, and an
        upstream kept as split files is read one file after another. Requires the input to exist.
        """
        paths = self._input_paths(name)
        if paths is None:
            file_path = self.input_file_path(name)
            if not file_path.exists():
                raise FileNotFoundError(f"Expected input file {file_path} does not exist.")
            paths = [file_path]
        for path in paths:
            if path.exists():
                for record in _jsonl.iter_json_lines(path):
                    yield from record.items()

    def _input_paths(self, name: str | None, /) -> list[pathlib.Path] | None:
        """The split files an upstream keeps its cache as, or `None` when it keeps the single file."""
        file_path = self.input_file_path(name)
        # An upstream that outgrew one file keeps it split across 16 or 256. Reading those whenever
        # the single file is absent means a downstream cache needs no change, before or after.
        if file_path.exists():
            return None
        for files in SPLIT_FILE_COUNTS:
            shard_paths = [file_path.with_name(shard) for shard in split_names(file_path.name, files)]
            if any(shard_path.exists() for shard_path in shard_paths):
                return shard_paths
        return None

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
        paths = self._existing_layout(self.output_file_path(name))
        return _jsonl.read_split_input(paths, format="lookup") if paths is not None else {}

    def iter_output(self, name: str | None = None, /) -> typing.Iterator[tuple[typing.Any, typing.Any]]:
        """Yield this cache's own recorded `(key, value)` entries one at a time, never all at once."""
        for path in self._existing_layout(self.output_file_path(name)) or []:
            if path.exists():
                for record in _jsonl.iter_json_lines(path):
                    yield from record.items()

    def merge_output_lookup(self, records: dict, name: str | None = None, /) -> pathlib.Path:
        """Add `records` to a split output, rewriting only the files they belong to.

        The rest of the output is never read, so a run holds only what it adds: that is what lets a
        cache too large for memory still be updated. An output not yet kept in its declared layout
        is read whole once and rewritten in it, which is its migration.
        """
        file_path = self.output_file_path(name)
        if not self._is_split(file_path):
            raise ValueError(
                f"{file_path.name} is not declared in `cache.split`; only a split output can be merged into."
            )
        shard_paths = self._shard_paths(file_path)
        if not all(shard_path.exists() for shard_path in shard_paths):
            combined = self.read_output_lookup(name)
            combined.update(records)
            return self.write_output_lookup(combined, name)

        prefixes = split_prefixes(self.config.split_files)
        touched: dict[str, dict] = {}
        for key, value in records.items():
            touched.setdefault(self._shard_of(key, file_path), {})[key] = value
        for prefix, shard_path in zip(prefixes, shard_paths, strict=True):
            if prefix in touched:
                shard = _jsonl.read_lookup(shard_path)
                shard.update(touched[prefix])
                _jsonl.write_lookup(shard_path, shard)
        return file_path

    def write_output_lookup(self, records: dict, name: str | None = None, /) -> pathlib.Path:
        """Write a `{key: value}` mapping to one of this cache's declared outputs.

        An output declared in `cache.split` is written across its sixteen files instead, and the
        returned path is the one file `dist` publishes it as.
        """
        file_path = self.output_file_path(name)
        if self._is_split(file_path):
            prefixes = split_prefixes(self.config.split_files)
            shards: dict[str, dict] = {prefix: {} for prefix in prefixes}
            for key in records:
                shards[self._shard_of(key, file_path)][key] = records[key]
            for prefix, shard_path in zip(prefixes, self._shard_paths(file_path), strict=True):
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
            prefixes = split_prefixes(self.config.split_files)
            shards: dict[str, list] = {prefix: [] for prefix in prefixes}
            for record in records:
                if not isinstance(record, dict) or len(record) != 1:
                    raise ValueError(f"A split output holds single-key records; {file_path.name} was given {record!r}.")
                shards[self._shard_of(next(iter(record)), file_path)].append(record)
            for prefix, shard_path in zip(prefixes, self._shard_paths(file_path), strict=True):
                _jsonl.write_records(shard_path, shards[prefix])
        else:
            _jsonl.write_records(file_path, records)
        self._remove_other_layout(file_path)
        return file_path

    def split_output_names(self, name: str, /) -> list[str]:
        """The files a split output is kept as: `<stem>_0.jsonl` to `<stem>_f.jsonl`, or `_00` to `_ff`."""
        return split_names(name, self.config.split_files)

    def _is_split(self, file_path: pathlib.Path, /) -> bool:
        return file_path.name.removeprefix(self.log_prefix) in self.config.split

    def _shard_paths(self, file_path: pathlib.Path, /) -> list[pathlib.Path]:
        return [file_path.with_name(shard) for shard in self.split_output_names(file_path.name)]

    def _shard_of(self, key, file_path: pathlib.Path, /) -> str:
        width = 1 if self.config.split_files == 16 else 2
        prefix = str(key)[:width].lower()
        if len(prefix) != width or any(character not in SPLIT_PREFIXES for character in prefix):
            raise ValueError(
                f"{key!r} does not start with {width} hexadecimal digit(s), so it belongs to none of the files "
                f"{file_path.name} is split across. Only a cache keyed by content ID can be split."
            )
        return prefix

    def _existing_layout(self, file_path: pathlib.Path, /) -> list[pathlib.Path] | None:
        """The files the output is kept as now: the declared layout first, then any it was kept as before.

        Reading whichever exists is what lets changing `split` or `split_files` migrate on the next
        run rather than lose what was recorded.
        """
        for paths in self._layouts(file_path):
            if any(path.exists() for path in paths):
                return paths
        return None

    def _layouts(self, file_path: pathlib.Path, /) -> list[list[pathlib.Path]]:
        """Every way the output can be kept on disk, the declared one first."""
        layouts = [[file_path]] + [
            [file_path.with_name(shard) for shard in split_names(file_path.name, files)] for files in SPLIT_FILE_COUNTS
        ]
        declared = self._shard_paths(file_path) if self._is_split(file_path) else [file_path]
        return [declared] + [layout for layout in layouts if layout != declared]

    def _remove_other_layout(self, file_path: pathlib.Path, /) -> None:
        """Remove what the output was kept as before, so a change of layout leaves one copy."""
        for layout in self._layouts(file_path)[1:]:
            for path in layout:
                path.unlink(missing_ok=True)
