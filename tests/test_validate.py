"""Tests for validating a Zarr store of JSON Lines, and for having `zarr` 3 read it."""

import json
import pathlib
import subprocess
import sys

import pytest
from click.testing import CliRunner

from dandi_cache_utils import _cli, _validate, _zarr

CHUNK = 1_000


def lines(count: int) -> list[str]:
    return [json.dumps({f"{index:06d}": f"A title number {index}"}) for index in range(count)]


def make_store(tmp_path: pathlib.Path, *, compressed: bool = False) -> pathlib.Path:
    store = tmp_path / "out.zarr"
    _zarr.write_fresh(store, lines(100), chunk_bytes=CHUNK, compressed=compressed)
    return store


@pytest.mark.ai_generated
@pytest.mark.parametrize("compressed", [False, True])
def test_a_store_written_by_the_library_has_no_problems(tmp_path, compressed):
    assert _validate.problems_with_store(make_store(tmp_path, compressed=compressed)) == []


@pytest.mark.ai_generated
def test_stores_are_found_below_a_directory(tmp_path):
    store = make_store(tmp_path / "derivatives")
    assert _validate.find_stores(tmp_path) == [store]
    assert _validate.find_stores(store) == [store]


@pytest.mark.ai_generated
def test_a_record_cut_across_chunks_is_reported(tmp_path):
    store = make_store(tmp_path)
    first = _zarr._chunk_path(store, 0)
    data = first.read_bytes().rstrip(b"\n")
    first.write_bytes((data[:-10]).ljust(CHUNK, b"\n"))
    assert any("not JSON" in problem for problem in _validate.problems_with_store(store))


@pytest.mark.ai_generated
def test_nul_bytes_missing_chunks_and_stray_data_are_reported(tmp_path):
    store = make_store(tmp_path)
    count = len(list((store / "stream" / "c").iterdir()))
    _zarr._chunk_path(store, 0).write_bytes(b"\x00" * CHUNK)
    _zarr._chunk_path(store, 1).unlink()
    (store / "stream" / "c" / str(count + 3)).write_bytes(b"\n")
    problems = _validate.problems_with_store(store)

    assert any("NUL" in problem for problem in problems)
    assert any("Chunk 1 is missing" in problem for problem in problems)
    assert any("past the end" in problem for problem in problems)


@pytest.mark.ai_generated
def test_metadata_that_disagrees_with_itself_is_reported(tmp_path):
    store = make_store(tmp_path)
    root = json.loads((store / "zarr.json").read_text())
    root["consolidated_metadata"]["metadata"]["stream"]["shape"] = [1]
    (store / "zarr.json").write_text(json.dumps(root))
    assert any("consolidated" in problem for problem in _validate.problems_with_store(store))

    (store / "zarr.json").unlink()
    assert any("cannot be read" in problem for problem in _validate.problems_with_store(store))


@pytest.mark.ai_generated
def test_the_command_passes_a_good_store_and_fails_a_bad_one(tmp_path):
    store = make_store(tmp_path / "derivatives")
    runner = CliRunner()
    good = runner.invoke(_cli.dandi_cache_cli, ["validate", str(tmp_path)])
    assert good.exit_code == 0, good.output
    assert "well formed" in good.output

    _zarr._chunk_path(store, 0).write_bytes(b"not json" + b"\n" * 10)
    bad = runner.invoke(_cli.dandi_cache_cli, ["validate", str(tmp_path)])
    assert bad.exit_code == 1
    assert "::error title=Invalid Zarr store" in bad.output


@pytest.mark.ai_generated
@pytest.mark.parametrize("compressed", [False, True])
def test_zarr_python_reads_the_store_as_the_files_bytes(tmp_path, compressed):
    zarr = pytest.importorskip("zarr")
    if int(zarr.__version__.split(".")[0]) < 3:
        pytest.skip("Needs zarr 3.")
    store = make_store(tmp_path, compressed=compressed)
    script = pathlib.Path(_validate.__file__).with_name("_zarr_crosscheck.py")

    result = subprocess.run([sys.executable, "-I", str(script), str(store)], capture_output=True, text=True)
    assert (result.returncode, result.stdout) == (0, "\n")

    # Metadata that claims the other codec chain than the chunk files were written with.
    wrong = _zarr._RAW_CODECS if compressed else _zarr._GZIP_CODECS
    root = json.loads((store / "zarr.json").read_text())
    root["consolidated_metadata"]["metadata"]["stream"]["codecs"] = wrong
    (store / "zarr.json").write_text(json.dumps(root))
    result = subprocess.run([sys.executable, "-I", str(script), str(store)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "zarr" in result.stdout
