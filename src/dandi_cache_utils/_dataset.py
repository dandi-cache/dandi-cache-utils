"""The on-disk layout of a cache, and the reads and writes that go with it.

Every cache is the same BIDS-ish study dataset: upstream caches are mounted read-only under
`sourcedata/`, results are written to `derivatives/`, and logs land in `logs/`. The pipeline
mounts that dataset at `/tmp` inside the container and passes it as `--base-directory`, so all
paths are resolved from one place and nothing is hard-coded per cache.

Every output is kept as a Zarr store, `derivatives/<stem>.zarr`, that only grows by appending; see
`dandi_cache_utils._zarr`. An output that an older version of the library kept as one JSONL file, or
split across 16, 256 or 4096 files by key prefix, is read once and moved into its store by the first
write, and an upstream cache that has not moved yet is still read as it is.

Testing mode redirects every output to a `testing_`-prefixed name. The real cache is therefore
untouched by a smoke run, and because `cache.toml` declares exactly which outputs are published, a
testing artifact can never reach the `dist` branch even if one is left behind.
"""

import dataclasses
import json
import pathlib
import typing

from . import _archive, _jsonl, _remote, _zarr
from ._config import DEFAULT_OPERATION, SPLIT_FILE_COUNTS, CacheConfig, InputCache, load_config, split_names
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
        """Open the dataset at `base_directory`, loading `cache.toml` if one was not supplied.

        This is also where the S3 helpers and the API client are pointed at the archive the cache
        describes, so a cache's own code reads from the right bucket without naming it.
        """
        config = config if config is not None else load_config()
        _archive.select(config.archive)
        return cls(
            config=config,
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
        if input_cache.stream:
            return _read_remote_store(self._remote_store(input_cache), format=input_cache.format)
        store = self._input_store(name)
        if store is not None:
            return _read_store(store, format=input_cache.format)
        paths = self._input_paths(name)
        if paths is not None:
            return _jsonl.read_split_input(paths, format=input_cache.format)
        return _jsonl.read_input(self.input_file_path(name), format=input_cache.format, required=required)

    def iter_input(self, name: str | None = None, /) -> typing.Iterator[tuple[typing.Any, typing.Any]]:
        """Yield an upstream lookup cache's `(key, value)` entries one at a time, in key order.

        For an input too large to hold in memory whole: only one entry is parsed at a time, and an
        upstream kept as split files is read one file after another, and one kept as a Zarr store one
        chunk after another. Requires the input to exist.

        A streamed input is read over HTTPS one chunk at a time, newest entries first.
        """
        input_cache: InputCache = self.config.only_input if name is None else self.config.input(name)
        if input_cache.stream:
            for line in self._remote_store(input_cache).iter_latest_lines():
                yield from json.loads(line).items()
            return
        store = self._input_store(name)
        if store is not None:
            yield from _iter_store_items(store)
            return
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

    def _remote_store(self, input_cache: InputCache, /) -> "_remote.RemoteStore":
        """A streamed input's store, at the upstream commit this dataset records for it."""
        commit = _remote.pinned_commit(self.base_directory, input_cache.path)
        store_name = _zarr.store_path(pathlib.Path(input_cache.file_name)).name
        return _remote.RemoteStore.at_commit(input_cache.url, commit, f"derivatives/{store_name}")

    def _input_store(self, name: str | None, /) -> pathlib.Path | None:
        """The Zarr store an upstream keeps its cache as, or `None` when it keeps files."""
        file_path = self.input_file_path(name)
        store = _zarr.store_path(file_path)
        return store if not file_path.exists() and _zarr.exists(store) else None

    def _input_paths(self, name: str | None, /) -> list[pathlib.Path] | None:
        """The split files an upstream keeps its cache as, or `None` when it keeps the single file."""
        file_path = self.input_file_path(name)
        # TODO: remove this legacy-layout support a couple of weeks after every cache has moved to Zarr stores.
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
        """The path of one of this cache's declared outputs by name, redirected in testing mode.

        This is the `.jsonl` file the output used to be kept as. It no longer exists once the output
        has been written, and `output_store_path` is where the output is.
        """
        file_name = self.config.cache_file_name if name is None else name
        if file_name not in self.config.outputs:
            declared = ", ".join(self.config.outputs)
            raise KeyError(f"{file_name!r} is not a declared output of {self.config.name} (declared: {declared}).")
        return self.derivatives_directory / f"{self.log_prefix}{file_name}"

    def output_store_path(self, name: str | None = None, /) -> pathlib.Path:
        """The Zarr store one of this cache's declared outputs is kept in."""
        return _zarr.store_path(self.output_file_path(name))

    def read_output_lookup(self, name: str | None = None, /) -> dict:
        """Read this cache's own prior output as a `{key: value}` mapping, the last entry for a key winning.

        This is the basis of every incremental update: what is already recorded here is what the
        run does not need to do again. An output not yet moved into its store is read from the file
        or files it was kept as, which is the whole of its first run's migration.
        """
        source = self._existing_source(self.output_file_path(name))
        if source is None:
            return {}
        if isinstance(source, pathlib.Path):
            return dict(_iter_store_items(source))
        return _jsonl.read_split_input(source, format="lookup")

    def iter_output(self, name: str | None = None, /) -> typing.Iterator[tuple[typing.Any, typing.Any]]:
        """Yield this cache's own recorded `(key, value)` entries one at a time, never all at once.

        These are the current entries only: a key replaced by a later line is yielded once, with its
        later value.
        """
        source = self._existing_source(self.output_file_path(name))
        if isinstance(source, pathlib.Path):
            yield from _iter_store_items(source)
            return
        for path in source or []:
            if path.exists():
                for record in _jsonl.iter_json_lines(path):
                    yield from record.items()

    def read_output_ids(self, name: str | None = None, /) -> set:
        """Read an output of bare scalars, one per line, as a set."""
        source = self._existing_source(self.output_file_path(name))
        if source is None:
            return set()
        if isinstance(source, pathlib.Path):
            return _read_store(source, format="ids")
        return _jsonl.read_split_input(source, format="ids")

    def merge_output_lookup(self, records: typing.Mapping, name: str | None = None, /) -> pathlib.Path:
        """Append `records` to an output, whether or not they replace entries it already has.

        The rest of the output is never read, so a run holds only what it adds: that is what lets a
        cache too large for memory still be updated. An output not yet in its store is moved into
        it first, which is its migration. Returns the output's store.
        """
        file_path = self.output_file_path(name)
        store = _zarr.store_path(file_path)
        self._migrate_to_store(file_path)
        _zarr.append_lines(
            store,
            (json.dumps({key: value}) for key, value in records.items()),
            chunk_bytes=self.config.chunk_bytes,
        )
        return store

    def write_output_lookup(self, records: typing.Mapping, name: str | None = None, /) -> pathlib.Path:
        """Record a whole `{key: value}` mapping in an output, appending only what differs from it.

        An entry the mapping no longer has cannot be expressed by appending, so then the output is
        written afresh, sorted by key; otherwise nothing already in it is touched.
        """
        file_path = self.output_file_path(name)
        store = _zarr.store_path(file_path)
        existing = self.read_output_lookup(name)
        if any(key not in records for key in existing):
            _zarr.write_fresh(
                store,
                (json.dumps({key: records[key]}) for key in sorted(records)),
                chunk_bytes=self.config.chunk_bytes,
            )
            self._remove_legacy_files(file_path)
            return store
        changed = {key: value for key, value in records.items() if key not in existing or existing[key] != value}
        return self.merge_output_lookup(changed, name)

    def write_output_records(self, records: typing.Iterable, name: str | None = None, /) -> pathlib.Path:
        """Record a list of JSON values, one per line, in an output.

        Records that are all single-key objects with different keys are a lookup, and are recorded
        like one. Any other list is kept in the order given, appending when what the output holds is
        the beginning of it and writing it afresh otherwise.
        """
        records = list(records)
        keys = [next(iter(record)) for record in records if isinstance(record, dict) and len(record) == 1]
        if len(keys) == len(records) and len(set(keys)) == len(keys):
            return self.write_output_lookup({key: record[key] for key, record in zip(keys, records)}, name)

        file_path = self.output_file_path(name)
        store = _zarr.store_path(file_path)
        lines = [json.dumps(record) for record in records]
        existing = [json.dumps(json.loads(line)) for line in _zarr.iter_lines(store)] if _zarr.exists(store) else None
        if existing is not None and lines[: len(existing)] == existing:
            _zarr.append_lines(store, lines[len(existing) :], chunk_bytes=self.config.chunk_bytes)
        else:
            _zarr.write_fresh(store, lines, chunk_bytes=self.config.chunk_bytes)
            self._remove_legacy_files(file_path)
        return store

    def write_output_ids(self, identifiers: typing.Iterable, name: str | None = None, /) -> pathlib.Path:
        """Record a set of bare scalars, one per line, appending the ones the output does not have yet."""
        file_path = self.output_file_path(name)
        store = _zarr.store_path(file_path)
        identifiers = set(identifiers)
        existing = self.read_output_ids(name)
        if existing - identifiers:
            _zarr.write_fresh(
                store, (json.dumps(value) for value in sorted(identifiers)), chunk_bytes=self.config.chunk_bytes
            )
            self._remove_legacy_files(file_path)
            return store
        self._migrate_to_store(file_path)
        _zarr.append_lines(
            store, (json.dumps(value) for value in sorted(identifiers - existing)), chunk_bytes=self.config.chunk_bytes
        )
        return store

    def compact_output(self, name: str | None = None, /) -> bool:
        """Rewrite an output's store without the lines a later line for the same key supersedes.

        Returns whether anything was done.
        """
        store = self.output_store_path(name)
        if not _zarr.exists(store):
            return False
        total, current = _zarr.count_lines(store)
        if total == current:
            return False
        _zarr.compact(store)
        return True

    def compact_output_if_superseded(
        self, name: str | None = None, /, *, fraction: float = 0.2, minimum: int = 1000
    ) -> bool:
        """Compact an output's store once at least `fraction` of its lines, and `minimum`, are superseded.

        Compacting rewrites most of the store's chunks, which shows in the branch's history as a large
        change, so it is left until the superseded lines are enough to be worth it. A cache calls this
        after its last write; `run_incremental_update` does.
        """
        store = self.output_store_path(name)
        if not _zarr.exists(store):
            return False
        total, current = _zarr.count_lines(store)
        superseded = total - current
        if superseded < minimum or superseded < fraction * total:
            return False
        _zarr.compact(store)
        return True

    def _existing_source(self, file_path: pathlib.Path, /) -> pathlib.Path | list[pathlib.Path] | None:
        """Where the output is kept now: its store, or else the files it was kept as before it had one."""
        store = _zarr.store_path(file_path)
        return store if _zarr.exists(store) else self._legacy_files(file_path)

    # TODO: remove this legacy-layout support a couple of weeks after every cache has moved to Zarr stores.
    def _legacy_files(self, file_path: pathlib.Path, /) -> list[pathlib.Path] | None:
        """The files an output was kept as before it became a store, if any of them exist."""
        for paths in self._legacy_layouts(file_path):
            if any(path.exists() for path in paths):
                return paths
        return None

    # TODO: remove this legacy-layout support a couple of weeks after every cache has moved to Zarr stores.
    def _legacy_layouts(self, file_path: pathlib.Path, /) -> list[list[pathlib.Path]]:
        """Every way an output used to be kept as files: one file, or split across 16, 256 or 4096."""
        return [[file_path]] + [
            [file_path.with_name(shard) for shard in split_names(file_path.name, files)] for files in SPLIT_FILE_COUNTS
        ]

    # TODO: remove this legacy-layout support a couple of weeks after every cache has moved to Zarr stores.
    def _migrate_to_store(self, file_path: pathlib.Path, /) -> None:
        """Make a store out of the files the output was kept as, when it is not a store yet."""
        store = _zarr.store_path(file_path)
        if _zarr.exists(store):
            return
        files = self._legacy_files(file_path)
        lines = (
            line
            for path in files or []
            if path.exists()
            for record in _jsonl.iter_json_lines(path)
            for line in _store_lines(record)
        )
        _zarr.write_fresh(store, lines, chunk_bytes=self.config.chunk_bytes)
        self._remove_legacy_files(file_path)

    # TODO: remove this legacy-layout support a couple of weeks after every cache has moved to Zarr stores.
    def _remove_legacy_files(self, file_path: pathlib.Path, /) -> None:
        """Remove the files an output used to be kept as, so a moved output leaves one copy."""
        for layout in self._legacy_layouts(file_path):
            for path in layout:
                path.unlink(missing_ok=True)


# TODO: remove this legacy-layout support a couple of weeks after every cache has moved to Zarr stores.
def _store_lines(record: typing.Any, /) -> typing.Iterator[str]:
    """The store lines for one line of a legacy file: an entry per key of a mapping, or a bare ID as it is."""
    if isinstance(record, dict):
        yield from (json.dumps({key: value}) for key, value in record.items())
    else:
        yield json.dumps(record)


def _iter_store_items(store: pathlib.Path, /) -> typing.Iterator[tuple[typing.Any, typing.Any]]:
    """The current `(key, value)` entries of a lookup kept as a Zarr store."""
    for line in _zarr.iter_latest_lines(store):
        yield from json.loads(line).items()


def _read_remote_store(store: "_remote.RemoteStore", /, *, format: str) -> dict | list | set:
    """Read a streamed input in whichever of the three shapes its cache declares."""
    if format == "lookup":
        return dict(item for line in store.iter_latest_lines() for item in json.loads(line).items())
    values = [json.loads(raw) for raw in store.iter_raw_lines()]
    if format == "records":
        return values
    if format == "ids":
        return set(values)
    raise ValueError(f"Unknown input format {format!r}; expected one of: ids, lookup, records.")


def _read_store(store: pathlib.Path, /, *, format: str) -> dict | list | set:
    """Read a Zarr store in whichever of the three shapes its cache declares."""
    if format == "lookup":
        return dict(_iter_store_items(store))
    values = [json.loads(line) for line in _zarr.iter_lines(store)]
    if format == "records":
        return values
    if format == "ids":
        return set(values)
    raise ValueError(f"Unknown input format {format!r}; expected one of: ids, lookup, records.")
