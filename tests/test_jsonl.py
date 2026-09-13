"""Tests for the JSON Lines shapes every cache reads and writes."""

import gzip
import json

import pytest

from dandi_cache_utils import jsonl


def test_a_lookup_merges_single_key_objects(tmp_path):
    file_path = tmp_path / "input.jsonl"
    file_path.write_text('{"a": 1}\n\n{"b": 2}\n{"c": 3}\n')

    assert jsonl.read_lookup(file_path) == {"a": 1, "b": 2, "c": 3}


def test_records_keep_file_order(tmp_path):
    file_path = tmp_path / "input.jsonl"
    file_path.write_text('{"z": 1}\n{"a": 2}\n')

    assert jsonl.read_records(file_path) == [{"z": 1}, {"a": 2}]


def test_ids_are_bare_scalars(tmp_path):
    file_path = tmp_path / "input.jsonl"
    file_path.write_text('"first"\n"second"\n')

    assert jsonl.read_ids(file_path) == {"first", "second"}


def test_a_missing_file_reads_as_empty(tmp_path):
    missing = tmp_path / "absent.jsonl"

    assert jsonl.read_lookup(missing) == {}
    assert jsonl.read_records(missing) == []
    assert jsonl.read_ids(missing) == set()


def test_a_required_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        jsonl.read_lookup(tmp_path / "absent.jsonl", required=True)


def test_gzipped_input_is_read_transparently(tmp_path):
    file_path = tmp_path / "input.jsonl.gz"
    with gzip.open(file_path, mode="wt") as file_stream:
        file_stream.write('{"a": 1}\n')

    assert jsonl.read_lookup(file_path) == {"a": 1}


def test_a_lookup_is_written_sorted_one_key_per_line(tmp_path):
    file_path = tmp_path / "out" / "cache.jsonl"

    jsonl.write_lookup(file_path, {"c": 3, "a": 1, "b": 2})

    assert file_path.read_text() == '{"a": 1}\n{"b": 2}\n{"c": 3}\n'


def test_a_lookup_round_trips(tmp_path):
    file_path = tmp_path / "cache.jsonl"
    records = {"a": {"000001": "sub-x/file.nwb"}, "b": False, "c": 17}

    jsonl.write_lookup(file_path, records)

    assert jsonl.read_lookup(file_path) == records


def test_compression_is_reproducible(tmp_path):
    file_path = tmp_path / "cache.jsonl"
    file_path.write_text('{"a": 1}\n')

    first = jsonl.compress(file_path).read_bytes()
    second = jsonl.compress(file_path).read_bytes()

    assert first == second
    assert gzip.decompress(first) == b'{"a": 1}\n'


def test_every_derivative_is_compressed(tmp_path):
    derivatives = tmp_path / "derivatives"
    derivatives.mkdir()
    (derivatives / "one.jsonl").write_text('{"a": 1}\n')
    (derivatives / "two.jsonl").write_text('{"b": 2}\n')
    (derivatives / "notes.txt").write_text("ignored\n")

    compressed = jsonl.compress_derivatives(tmp_path)

    assert [path.name for path in compressed] == ["one.jsonl.gz", "two.jsonl.gz"]


def test_read_input_dispatches_on_the_declared_format(tmp_path):
    file_path = tmp_path / "input.jsonl"
    file_path.write_text('{"a": 1}\n')

    assert jsonl.read_input(file_path, format="lookup") == {"a": 1}
    assert jsonl.read_input(file_path, format="records") == [{"a": 1}]

    with pytest.raises(ValueError, match="Unknown input format"):
        jsonl.read_input(file_path, format="yaml")


def test_records_are_written_one_json_value_per_line(tmp_path):
    file_path = tmp_path / "cache.jsonl"

    written = jsonl.write_records(file_path, [{"a": 1}, {"b": 2}])

    assert written == 2
    assert [json.loads(line) for line in file_path.read_text().splitlines()] == [{"a": 1}, {"b": 2}]
