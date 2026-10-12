"""Check that a Zarr store of JSON Lines is well formed, and that a Zarr library reads it as we do.

Two checks, because they fail differently:

- `problems_with_store` is standard library only. It checks the store against the layout this
  package promises: consistent metadata, chunks that are whole lines of JSON (an object per line, or
  a bare ID such as a string), nothing but newlines
  after the last record. It is cheap enough to run on every store before every push.
- `cross_check_with_zarr_python` asks the `zarr` package (version 3) to read the same store and
  compares what it decodes, chunk by chunk, with what this package reads. That is what shows a store
  is a real Zarr store and not just something shaped like one. `zarr` 3 cannot be installed beside
  the `zarr<3` the NWB stack needs, so it runs in a throwaway virtual environment, through the
  standalone `_zarr_crosscheck.py`.
"""

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

from . import _zarr


def find_stores(directory: pathlib.Path, /) -> list[pathlib.Path]:
    """Every Zarr store of JSON Lines at or below `directory`, in name order."""
    if _zarr.exists(directory):
        return [directory]
    return sorted(path.parent.parent for path in directory.rglob(f"{_zarr.ARRAY_NAME}/zarr.json"))


def problems_with_store(store: pathlib.Path, /) -> list[str]:
    """What is wrong with the store, one sentence each; an empty list means it is well formed."""
    problems: list[str] = []
    root_path = store / "zarr.json"
    try:
        root = json.loads(root_path.read_text())
        array = _zarr._read_array_metadata(store)
    except (OSError, ValueError) as error:
        return [f"The group or array metadata cannot be read: {error}."]

    if root.get("node_type") != "group" or root.get("zarr_format") != 3:
        problems.append("The root `zarr.json` is not a Zarr v3 group.")
    if root.get("consolidated_metadata", {}).get("metadata", {}).get(_zarr.ARRAY_NAME) != array:
        problems.append("The consolidated metadata in the root `zarr.json` differs from the array's own `zarr.json`.")
    try:
        (length,) = array["shape"]
        chunk_bytes = _zarr._chunk_bytes(array)
    except (KeyError, TypeError, ValueError):
        return [*problems, "The array is not one-dimensional with a regular chunk grid."]
    codec_names = [codec["name"] for codec in array.get("codecs", [])]
    if (
        array.get("data_type") != "uint8"
        or array.get("fill_value") != _zarr.NEWLINE
        or codec_names not in (["bytes"], ["bytes", "gzip"])
    ):
        problems.append("The array is not `uint8` with fill value 10 (a newline) and the `bytes` [+ `gzip`] codecs.")

    chunk_count = _zarr._chunk_count(length, chunk_bytes)
    chunk_dir = store / _zarr.ARRAY_NAME / "c"
    present = {path.name for path in chunk_dir.iterdir()} if chunk_dir.is_dir() else set()
    expected = {str(index) for index in range(chunk_count)}
    if extra := sorted(present - expected, key=lambda name: (len(name), name)):
        problems.append(f"Chunk files past the end of the array: {', '.join(extra[:5])}.")
    for index in range(chunk_count):
        problems.extend(_problems_with_chunk(store, index, length, chunk_bytes, last=index == chunk_count - 1))
        if len(problems) >= 20:
            problems.append("Stopped after 20 problems.")
            break
    return problems


def _problems_with_chunk(store: pathlib.Path, index: int, length: int, chunk_bytes: int, /, *, last: bool) -> list[str]:
    path = _zarr._chunk_path(store, index)
    where = f"Chunk {index}"
    if not path.is_file():
        return [f"{where} is missing."]
    try:
        data = _zarr._read_chunk(path)
    except (OSError, EOFError) as error:
        return [f"{where} cannot be decompressed: {error}."]
    if len(data) > chunk_bytes:
        return [f"{where} is {len(data):,} bytes, past the chunk size of {chunk_bytes:,}."]
    if b"\x00" in data:
        return [f"{where} contains NUL bytes."]
    if last:
        used = length - index * chunk_bytes
        if data[used:].strip(b"\n"):
            return [f"{where} holds something other than newlines after the end of the array."]
        data = data[:used]
    for number, raw in enumerate(data.rstrip(b"\n").split(b"\n"), start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            return [f"{where}, line {number} is not JSON, so a record may have been cut across chunks."]
        # A lookup or records output holds an object per line; an output of IDs, a bare scalar.
        if isinstance(record, list):
            return [f"{where}, line {number} is a JSON array, which is neither a record nor a bare ID."]
    return []


def cross_check_with_zarr_python(
    stores: list[pathlib.Path], /, *, environment: pathlib.Path | None = None
) -> tuple[list[str], str | None]:
    """Have `zarr` 3 read each store, and return `(problems, unavailable)`.

    `unavailable` is a reason the check could not run at all, as opposed to having found a problem:
    no way to build the environment, or no network to install `zarr` into it.
    """
    owns_environment = environment is None
    environment = environment or pathlib.Path(tempfile.mkdtemp(prefix="zarr-crosscheck-"))
    try:
        python = _prepare_environment(environment)
        if isinstance(python, str):
            return [], python
        script = pathlib.Path(__file__).with_name("_zarr_crosscheck.py")
        result = subprocess.run(
            [str(python), "-I", str(script), *map(str, stores)], capture_output=True, text=True, check=False
        )
        problems = [line for line in result.stdout.splitlines() if line]
        if result.returncode != 0 and not problems:
            problems = [f"The cross-check failed: {result.stderr.strip()[-500:] or result.returncode}."]
        return problems, None
    finally:
        if owns_environment:
            shutil.rmtree(environment, ignore_errors=True)


def _prepare_environment(environment: pathlib.Path, /) -> pathlib.Path | str:
    """The interpreter of an environment holding `zarr>=3`, building it when needed; or why it cannot be."""
    python = environment / "bin" / "python"
    if python.exists():
        return python
    uv = shutil.which("uv")
    steps = (
        [[uv, "venv", "--python", sys.executable, str(environment)]]
        + [[uv, "pip", "install", "--python", str(python), "zarr>=3"]]
        if uv
        else [
            [sys.executable, "-m", "venv", str(environment)],
            [str(python), "-m", "pip", "install", "--quiet", "zarr>=3"],
        ]
    )
    for step in steps:
        result = subprocess.run(step, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            shutil.rmtree(environment, ignore_errors=True)
            return f"`{' '.join(step[:3])}` failed: {result.stderr.strip()[-300:]}"
    return python
