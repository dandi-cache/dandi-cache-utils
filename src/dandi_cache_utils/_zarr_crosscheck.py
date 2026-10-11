"""Standalone: have the `zarr` package (version 3) read stores of JSON Lines and compare.

Run with an interpreter that has `zarr>=3`, which `_validate.cross_check_with_zarr_python` arranges.
It imports nothing from this package, so what it checks is the store and not this package's reading
of it: each chunk file is read here directly, and the `zarr` library must decode exactly the same
bytes for the chunk's span of the array. Prints one line per problem; exits 1 if there were any.
"""

import gzip
import json
import pathlib
import sys


def check(store: pathlib.Path, /) -> list[str]:
    import zarr

    group = zarr.open_group(store, mode="r", use_consolidated=True)
    array = group["stream"]
    (length,) = array.shape
    (chunk_bytes,) = array.chunks
    problems = []
    if json.loads((store / "stream" / "zarr.json").read_text())["shape"] != [length]:
        problems.append(f"{store}: zarr reports a shape different from the array metadata.")
    for index in range(-(-length // chunk_bytes)):
        start, stop = index * chunk_bytes, min((index + 1) * chunk_bytes, length)
        path = store / "stream" / "c" / str(index)
        data = path.read_bytes() if path.exists() else b""
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        expected = data.ljust(chunk_bytes, b"\n")[: stop - start]
        if array[start:stop].tobytes() != expected:
            problems.append(f"{store}: zarr decodes chunk {index} differently from the file's bytes.")
            break
    return problems


def main(paths: list[str], /) -> int:
    problems = []
    for path in paths:
        try:
            problems.extend(check(pathlib.Path(path)))
        except Exception as error:  # noqa: BLE001 - any failure to open or read is a finding
            problems.append(f"{path}: zarr cannot read the store: {type(error).__name__}: {error}")
    print("\n".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
