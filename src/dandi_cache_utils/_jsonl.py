"""Reading and writing the JSON Lines files that every cache consumes and produces.

Three shapes are in use across the organization, and each was reimplemented in every repository
that needed it. They are all here now:

- **lookup**  one single-key object per line, merged into one `{key: value}` mapping. This is what
  a `<thing>-to-<other-thing>` cache publishes and what its downstreams read.
- **records** one independent JSON value per line, kept in order as a list.
- **ids**     one bare scalar per line, collected into a set.

Reads of a missing file return the empty container rather than raising: a cache's own output does
not exist before its first run, and treating that as "nothing recorded yet" is what makes every
update naturally incremental.
"""

import gzip
import json
import pathlib
import shutil
import typing

__all__ = [
    "compress",
    "compress_derivatives",
    "iter_json_lines",
    "read_ids",
    "read_input",
    "read_lookup",
    "read_records",
    "write_ids",
    "write_lookup",
    "write_records",
]


def iter_json_lines(file_path: pathlib.Path, /) -> typing.Iterator[typing.Any]:
    """Yield one parsed JSON value per non-blank line, transparently handling `.gz`."""
    opener = gzip.open if file_path.suffix == ".gz" else open
    with opener(file_path, mode="rt") as file_stream:
        for line in file_stream:
            if line.strip():
                yield json.loads(line)


def read_lookup(file_path: pathlib.Path, /, *, required: bool = False) -> dict:
    """Load a `{key: value}` mapping from a JSONL file of single-key objects.

    Returns an empty mapping when the file is missing, unless `required` is set -- use that for an
    input that must exist, where an empty read would silently produce an empty cache.
    """
    if not file_path.exists():
        if required:
            raise FileNotFoundError(f"Expected input file {file_path} does not exist.")
        return {}

    records: dict = {}
    for value in iter_json_lines(file_path):
        records.update(value)
    return records


def read_records(file_path: pathlib.Path, /, *, required: bool = False) -> list:
    """Load a list of JSON values, one per line, preserving file order."""
    if not file_path.exists():
        if required:
            raise FileNotFoundError(f"Expected input file {file_path} does not exist.")
        return []
    return list(iter_json_lines(file_path))


def read_ids(file_path: pathlib.Path, /, *, required: bool = False) -> set:
    """Load a set of bare scalars, one per line."""
    if not file_path.exists():
        if required:
            raise FileNotFoundError(f"Expected input file {file_path} does not exist.")
        return set()
    return set(iter_json_lines(file_path))


def read_input(file_path: pathlib.Path, /, *, format: str, required: bool = False) -> dict | list | set:
    """Read a file in whichever of the three shapes its cache declares in `cache.toml`."""
    readers = {"lookup": read_lookup, "records": read_records, "ids": read_ids}
    if format not in readers:
        raise ValueError(f"Unknown input format {format!r}; expected one of: {', '.join(sorted(readers))}.")
    return readers[format](file_path, required=required)


def write_lookup(file_path: pathlib.Path, records: typing.Mapping, /) -> None:
    """Write a `{key: value}` mapping as one single-key object per line, sorted by key.

    Sorting is what keeps the published artifact stable: an unsorted rewrite would show every line
    as changed in the `derivatives` history even when only one entry was added.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open(mode="w") as file_stream:
        file_stream.writelines(f"{json.dumps({key: records[key]})}\n" for key in sorted(records))


def write_records(file_path: pathlib.Path, records: typing.Iterable, /) -> int:
    """Write one JSON value per line, in the order given; return how many were written."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with file_path.open(mode="w") as file_stream:
        for record in records:
            file_stream.write(f"{json.dumps(record)}\n")
            written += 1
    return written


def write_ids(file_path: pathlib.Path, identifiers: typing.Iterable, /) -> None:
    """Write one bare scalar per line, sorted."""
    write_records(file_path, sorted(identifiers))


def compress(file_path: pathlib.Path, /) -> pathlib.Path:
    """Gzip one file next to itself and return the compressed path.

    `mtime=0` keeps the gzip header timestamp-free, so unchanged input compresses to a
    byte-identical artifact run after run -- without it, every run republishes `dist` as changed
    even when the cache did not move.
    """
    compressed_file_path = file_path.parent / f"{file_path.name}.gz"
    with (
        file_path.open(mode="rb") as source_stream,
        compressed_file_path.open(mode="wb") as raw_target_stream,
        gzip.GzipFile(fileobj=raw_target_stream, mode="wb", mtime=0) as target_stream,
    ):
        shutil.copyfileobj(fsrc=source_stream, fdst=target_stream)
    return compressed_file_path


def compress_derivatives(base_directory: pathlib.Path, /) -> list[pathlib.Path]:
    """Gzip every `derivatives/*.jsonl` file for distribution; return the compressed paths."""
    derivatives_directory = base_directory / "derivatives"
    return [compress(jsonl_file_path) for jsonl_file_path in sorted(derivatives_directory.glob("*.jsonl"))]
