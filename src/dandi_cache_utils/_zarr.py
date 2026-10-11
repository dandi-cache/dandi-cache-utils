"""A log of JSON Lines kept as a Zarr v3 store, written and read with the standard library alone.

A cache's output can grow past what one file on a git branch may hold, and splitting it by key
prefix means choosing a width, bumping it as the output grows, and repacking every file when it
changes. This is the alternative: one store that only ever grows by appending.

The store is a Zarr group holding one 1-D `uint8` array, `stream`, whose bytes are the JSON Lines
text, chunked at a fixed size well under GitHub's limit for one file. With no compression codec and
`fill_value=10` (a newline), every chunk file `stream/c/0`, `stream/c/1`, ... is plain UTF-8 text,
and the unwritten tail of the last chunk reads as blank lines, never NUL bytes. So:

- **git treats it as text**, and an append changes only the last chunk and any new ones;
- **a record is never split across two chunks**: one that does not fit in what is left of the
  current chunk pads it with newlines and starts the next, so each chunk file is a self-contained
  set of whole lines, and a record can be at most one chunk long;
- **anything that reads JSON Lines can read it**: concatenate the chunk files in order and skip the
  blank lines, with no Zarr library at all;
- **anything that reads Zarr can read it**: the group's `zarr.json` carries consolidated metadata,
  refreshed after every append, so a reader learns the layout from one small file. For a store
  opened over HTTP that is one request instead of one per node.

A replaced entry is a new line appended, and the last line for a key wins; `compact` rewrites the
store without the superseded lines. The published copy on `dist` is such a compacted store with each
chunk gzip-compressed, which is a Zarr codec like any other.

This writer does not depend on the `zarr` package, whose use would put it in the runner's
bootstrap, and the tests open what it writes with that package to keep the two in step.
"""

import gzip
import json
import pathlib
import shutil
import typing

from ._config import DEFAULT_CHUNK_BYTES as DEFAULT_ZARR_CHUNK_BYTES

#: The chunk size, in bytes, of a store that does not say otherwise. It leaves a chunk well under the
#: point at which `check-sizes` starts warning about GitHub's 100 MiB limit for one file.
DEFAULT_CHUNK_BYTES = DEFAULT_ZARR_CHUNK_BYTES

#: The name of the array inside the group.
ARRAY_NAME = "stream"

NEWLINE = 10
_ROOT_METADATA = "zarr.json"
_NEWLINE_BYTE = b"\n"


def store_path(output_path: pathlib.Path, /) -> pathlib.Path:
    """Where the Zarr store of a `<stem>.jsonl` output lives: `<stem>.zarr`, beside it."""
    return output_path.with_suffix(".zarr")


def exists(store: pathlib.Path, /) -> bool:
    """Whether `store` holds a store, as opposed to being absent or an empty directory."""
    return (store / ARRAY_NAME / "zarr.json").is_file()


def _array_metadata(length: int, chunk_bytes: int, codecs: list[dict], /) -> dict:
    return {
        "shape": [length],
        "data_type": "uint8",
        "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": [chunk_bytes]}},
        "chunk_key_encoding": {"name": "default", "configuration": {"separator": "/"}},
        "fill_value": NEWLINE,
        "codecs": codecs,
        "attributes": {},
        "zarr_format": 3,
        "node_type": "array",
        "storage_transformers": [],
    }


_RAW_CODECS = [{"name": "bytes"}]
_GZIP_CODECS = [{"name": "bytes"}, {"name": "gzip", "configuration": {"level": 9}}]

_GROUP_ATTRIBUTES = {
    "dandi_cache": {
        "layout": "jsonl-log",
        "description": (
            "The array `stream` holds JSON Lines text, one record per line, in chunks that each end on a whole "
            "record and are padded with newlines. Concatenate the chunks in order and skip blank lines to read it "
            "as JSONL. Where a key appears more than once, the last line is the current entry."
        ),
    }
}


def _write_metadata(store: pathlib.Path, length: int, chunk_bytes: int, codecs: list[dict], /) -> None:
    array = _array_metadata(length, chunk_bytes, codecs)
    store.mkdir(parents=True, exist_ok=True)
    (store / ARRAY_NAME).mkdir(exist_ok=True)
    (store / ARRAY_NAME / "zarr.json").write_text(json.dumps(array, indent=2) + "\n")
    root = {
        "attributes": _GROUP_ATTRIBUTES,
        "zarr_format": 3,
        "consolidated_metadata": {"kind": "inline", "must_understand": False, "metadata": {ARRAY_NAME: array}},
        "node_type": "group",
    }
    (store / _ROOT_METADATA).write_text(json.dumps(root, indent=2) + "\n")


def _read_array_metadata(store: pathlib.Path, /) -> dict:
    return json.loads((store / ARRAY_NAME / "zarr.json").read_text())


def _chunk_bytes(metadata: dict, /) -> int:
    return metadata["chunk_grid"]["configuration"]["chunk_shape"][0]


def _chunk_path(store: pathlib.Path, index: int, /) -> pathlib.Path:
    return store / ARRAY_NAME / "c" / str(index)


def _aligned_payload(lines: typing.Iterable[str], position: int, chunk_bytes: int, /) -> bytes:
    """The bytes to write at `position`: each record whole, padding to the next chunk when it will not fit."""
    payload = bytearray()
    for line in lines:
        record = (line if line.endswith("\n") else f"{line}\n").encode("utf-8")
        if len(record) > chunk_bytes:
            raise ValueError(
                f"A record of {len(record):,} bytes does not fit in a chunk of {chunk_bytes:,}; "
                "raise `chunk_bytes` in `cache.toml`, within GitHub's 100 MiB limit for one file."
            )
        used = (position + len(payload)) % chunk_bytes
        if used and len(record) > chunk_bytes - used:
            payload += _NEWLINE_BYTE * (chunk_bytes - used)
        payload += record
    return bytes(payload)


def append_lines(store: pathlib.Path, lines: typing.Iterable[str], /, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> int:
    """Append lines to the store, creating it when absent; return the number of bytes added.

    An existing store keeps its own chunk size, whatever `chunk_bytes` says. Only the chunk files the
    new bytes land in are touched, and the metadata is refreshed once at the end.
    """
    if exists(store):
        metadata = _read_array_metadata(store)
        chunk_bytes = _chunk_bytes(metadata)
        length = metadata["shape"][0]
    else:
        length = 0
    payload = _aligned_payload(lines, length, chunk_bytes)
    if not payload:
        if not exists(store):
            _write_metadata(store, 0, chunk_bytes, _RAW_CODECS)
        return 0

    (store / ARRAY_NAME / "c").mkdir(parents=True, exist_ok=True)
    written = 0
    while written < len(payload):
        position = length + written
        index, offset = divmod(position, chunk_bytes)
        part = payload[written : written + chunk_bytes - offset]
        path = _chunk_path(store, index)
        if not path.exists():
            path.write_bytes(_NEWLINE_BYTE * chunk_bytes)
        with path.open("r+b") as stream:
            stream.seek(offset)
            stream.write(part)
        written += len(part)
    _write_metadata(store, length + len(payload), chunk_bytes, _RAW_CODECS)
    return len(payload)


def _chunk_count(length: int, chunk_bytes: int, /) -> int:
    return -(-length // chunk_bytes)


def _read_chunk(path: pathlib.Path, /) -> bytes:
    data = path.read_bytes()
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def _iter_raw_lines(store: pathlib.Path, /) -> typing.Iterator[bytes]:
    """Yield the non-blank lines of the store as bytes, in order, one chunk at a time."""
    if not exists(store):
        return
    metadata = _read_array_metadata(store)
    for index in range(_chunk_count(metadata["shape"][0], _chunk_bytes(metadata))):
        path = _chunk_path(store, index)
        if not path.exists():
            continue
        # The padding is at the end of a chunk, so stripping it first leaves no empty lines to walk.
        for raw in _read_chunk(path).rstrip(_NEWLINE_BYTE).split(_NEWLINE_BYTE):
            if raw and not raw.isspace():
                yield raw


def iter_lines(store: pathlib.Path, /) -> typing.Iterator[str]:
    """Yield the non-blank lines of the store in order, one chunk at a time.

    Chunks written with the gzip codec, as the published copy on `dist` is, are read the same way.
    """
    for raw in _iter_raw_lines(store):
        yield raw.decode("utf-8")


def _raw_line_key(raw: bytes, /) -> typing.Any:
    """The key of a single-key JSON object line, parsing it only when the key needs unescaping."""
    if raw.startswith(b'{"'):
        end = raw.find(b'"', 2)
        if end != -1 and b"\\" not in raw[2:end]:
            return raw[2:end]
    (key,) = json.loads(raw)
    return key.encode("utf-8") if isinstance(key, str) else key


def line_key(line: str, /) -> typing.Any:
    """The key of a single-key JSON object line."""
    key = _raw_line_key(line.encode("utf-8"))
    return key.decode("utf-8") if isinstance(key, bytes) else key


def iter_latest_lines(store: pathlib.Path, /) -> typing.Iterator[str]:
    """Yield the lines of a lookup store that are current: the last line for each key, in order.

    Two passes, the first only noting where each key last appears without decoding the lines, so the
    memory held is a number per key rather than the entries.
    """
    last: dict = {}
    for ordinal, raw in enumerate(_iter_raw_lines(store)):
        last[_raw_line_key(raw)] = ordinal
    for ordinal, raw in enumerate(_iter_raw_lines(store)):
        if last.get(_raw_line_key(raw)) == ordinal:
            yield raw.decode("utf-8")


def count_lines(store: pathlib.Path, /) -> tuple[int, int]:
    """How many lines a lookup store holds, and how many of them are current."""
    keys = set()
    total = 0
    for raw in _iter_raw_lines(store):
        keys.add(_raw_line_key(raw))
        total += 1
    return total, len(keys)


def write_fresh(
    store: pathlib.Path,
    lines: typing.Iterable[str],
    /,
    *,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    compressed: bool = False,
) -> None:
    """Write `lines` as a store that replaces whatever is at `store`.

    `compressed` gzips every chunk and says so in the metadata, which is how `dist` publishes it.
    The gzip header carries no timestamp, so unchanged input compresses to identical files run after
    run, and republishing `dist` does not look like a change.
    """
    temporary = store.with_name(f"{store.name}.writing")
    shutil.rmtree(temporary, ignore_errors=True)
    (temporary / ARRAY_NAME / "c").mkdir(parents=True)

    index = 0
    length = 0
    chunk = bytearray()

    def flush() -> None:
        data = bytes(chunk).ljust(chunk_bytes, _NEWLINE_BYTE)
        if compressed:
            data = gzip.compress(data, compresslevel=9, mtime=0)
        _chunk_path(temporary, index).write_bytes(data)

    for line in lines:
        record = (line if line.endswith("\n") else f"{line}\n").encode("utf-8")
        if len(record) > chunk_bytes:
            raise ValueError(f"A record of {len(record):,} bytes does not fit in a chunk of {chunk_bytes:,}.")
        if chunk and len(chunk) + len(record) > chunk_bytes:
            length += chunk_bytes - len(chunk)
            flush()
            index += 1
            chunk = bytearray()
        chunk += record
        length += len(record)
    if chunk:
        flush()

    _write_metadata(temporary, length, chunk_bytes, _GZIP_CODECS if compressed else _RAW_CODECS)
    shutil.rmtree(store, ignore_errors=True)
    temporary.rename(store)


def compact(store: pathlib.Path, /) -> None:
    """Rewrite a lookup store without the lines a later line for the same key supersedes."""
    chunk_bytes = _chunk_bytes(_read_array_metadata(store))
    write_fresh(store, iter_latest_lines(store), chunk_bytes=chunk_bytes)


def publish(store: pathlib.Path, destination: pathlib.Path, /) -> pathlib.Path:
    """Write the copy of a lookup store that `dist` publishes into `destination`, and return its path.

    The copy is `<destination>/<store name>`: the store compacted, so a consumer never meets a
    superseded line, with every chunk gzip-compressed. It is a Zarr store like any other, opened over
    HTTP by anything that reads Zarr, and each chunk is also a gzip file of whole JSONL lines, so a
    consumer without a Zarr library can fetch the chunks in order and decompress them.
    """
    destination.mkdir(parents=True, exist_ok=True)
    published = destination / store.name
    write_fresh(
        published, iter_latest_lines(store), chunk_bytes=_chunk_bytes(_read_array_metadata(store)), compressed=True
    )
    return published
