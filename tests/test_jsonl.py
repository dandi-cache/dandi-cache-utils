"""Tests for the JSON Lines shapes every cache reads and writes."""

import gzip
import json

import pytest

import dandi_cache_utils as dandi_cache


@pytest.mark.ai_generated
def test_a_lookup_merges_single_key_objects(tmp_path):
    file_path = tmp_path / "input.jsonl"
    file_path.write_text('{"a": 1}\n\n{"b": 2}\n{"c": 3}\n')

    assert dandi_cache.read_lookup(file_path) == {"a": 1, "b": 2, "c": 3}


@pytest.mark.ai_generated
def test_records_keep_file_order(tmp_path):
    file_path = tmp_path / "input.jsonl"
    file_path.write_text('{"z": 1}\n{"a": 2}\n')

    assert dandi_cache.read_records(file_path) == [{"z": 1}, {"a": 2}]


@pytest.mark.ai_generated
def test_ids_are_bare_scalars(tmp_path):
    file_path = tmp_path / "input.jsonl"
    file_path.write_text('"first"\n"second"\n')

    assert dandi_cache.read_ids(file_path) == {"first", "second"}


@pytest.mark.ai_generated
def test_a_missing_file_reads_as_empty(tmp_path):
    missing = tmp_path / "absent.jsonl"

    assert dandi_cache.read_lookup(missing) == {}
    assert dandi_cache.read_records(missing) == []
    assert dandi_cache.read_ids(missing) == set()


@pytest.mark.ai_generated
def test_a_required_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        dandi_cache.read_lookup(tmp_path / "absent.jsonl", required=True)


@pytest.mark.ai_generated
def test_gzipped_input_is_read_transparently(tmp_path):
    file_path = tmp_path / "input.jsonl.gz"
    with gzip.open(file_path, mode="wt") as file_stream:
        file_stream.write('{"a": 1}\n')

    assert dandi_cache.read_lookup(file_path) == {"a": 1}


@pytest.mark.ai_generated
def test_a_lookup_is_written_sorted_one_key_per_line(tmp_path):
    file_path = tmp_path / "out" / "cache.jsonl"

    dandi_cache.write_lookup(file_path, {"c": 3, "a": 1, "b": 2})

    assert file_path.read_text() == '{"a": 1}\n{"b": 2}\n{"c": 3}\n'


@pytest.mark.ai_generated
def test_a_lookup_round_trips(tmp_path):
    file_path = tmp_path / "cache.jsonl"
    records = {"a": {"000001": "sub-x/file.nwb"}, "b": False, "c": 17}

    dandi_cache.write_lookup(file_path, records)

    assert dandi_cache.read_lookup(file_path) == records


@pytest.mark.ai_generated
def test_compression_is_reproducible(tmp_path):
    file_path = tmp_path / "cache.jsonl"
    file_path.write_text('{"a": 1}\n')

    first = dandi_cache.compress(file_path).read_bytes()
    second = dandi_cache.compress(file_path).read_bytes()

    assert first == second
    assert gzip.decompress(first) == b'{"a": 1}\n'


@pytest.mark.ai_generated
def test_every_derivative_is_compressed(tmp_path):
    derivatives = tmp_path / "derivatives"
    derivatives.mkdir()
    (derivatives / "one.jsonl").write_text('{"a": 1}\n')
    (derivatives / "two.jsonl").write_text('{"b": 2}\n')
    (derivatives / "notes.txt").write_text("ignored\n")

    compressed = dandi_cache.compress_derivatives(tmp_path)

    assert [path.name for path in compressed] == ["one.jsonl.gz", "two.jsonl.gz"]


@pytest.mark.ai_generated
def test_an_output_kept_as_sixteen_files_is_compressed_as_one(tmp_path):
    derivatives = tmp_path / "derivatives"
    derivatives.mkdir()
    for prefix in "0123456789abcdef":
        (derivatives / f"cache_{prefix}.jsonl").write_text("")
    (derivatives / "cache_0.jsonl").write_text('{"0a": 1}\n')
    (derivatives / "cache_f.jsonl").write_text('{"f1": 2}\n')
    (derivatives / "other.jsonl").write_text('{"b": 2}\n')

    compressed = dandi_cache.compress_derivatives(tmp_path)

    # Published under the name it had before it was split, and only under that name.
    assert [path.name for path in compressed] == ["cache.jsonl.gz", "other.jsonl.gz"]
    assert gzip.decompress((derivatives / "cache.jsonl.gz").read_bytes()) == b'{"0a": 1}\n{"f1": 2}\n'


@pytest.mark.ai_generated
def test_files_that_only_look_split_are_compressed_one_by_one(tmp_path):
    derivatives = tmp_path / "derivatives"
    derivatives.mkdir()
    # Two of sixteen is not a split output, and neither is a full set beside its own single file.
    (derivatives / "scores_0.jsonl").write_text("{}\n")
    (derivatives / "scores_1.jsonl").write_text("{}\n")

    compressed = dandi_cache.compress_derivatives(tmp_path)

    assert [path.name for path in compressed] == ["scores_0.jsonl.gz", "scores_1.jsonl.gz"]


@pytest.mark.ai_generated
def test_read_input_dispatches_on_the_declared_format(tmp_path):
    file_path = tmp_path / "input.jsonl"
    file_path.write_text('{"a": 1}\n')

    assert dandi_cache.read_input(file_path, format="lookup") == {"a": 1}
    assert dandi_cache.read_input(file_path, format="records") == [{"a": 1}]

    with pytest.raises(ValueError, match="Unknown input format"):
        dandi_cache.read_input(file_path, format="yaml")


@pytest.mark.ai_generated
def test_records_are_written_one_json_value_per_line(tmp_path):
    file_path = tmp_path / "cache.jsonl"

    written = dandi_cache.write_records(file_path, [{"a": 1}, {"b": 2}])

    assert written == 2
    assert [json.loads(line) for line in file_path.read_text().splitlines()] == [{"a": 1}, {"b": 2}]


@pytest.mark.ai_generated
def test_a_split_output_published_separately_is_compressed_file_by_file(tmp_path):
    derivatives = tmp_path / "derivatives"
    derivatives.mkdir()
    for prefix in "0123456789abcdef":
        (derivatives / f"cache_{prefix}.jsonl").write_text(f'{{"{prefix}1": 1}}\n')

    compressed = dandi_cache.compress_derivatives(tmp_path, separate={"cache.jsonl"})

    assert [path.name for path in compressed] == [f"cache_{prefix}.jsonl.gz" for prefix in "0123456789abcdef"]
    assert not (derivatives / "cache.jsonl.gz").exists()


@pytest.mark.ai_generated
def test_an_output_kept_as_256_files_is_joined_by_default(tmp_path):
    derivatives = tmp_path / "derivatives"
    derivatives.mkdir()
    for first in "0123456789abcdef":
        for second in "0123456789abcdef":
            (derivatives / f"cache_{first}{second}.jsonl").write_text("")
    (derivatives / "cache_ff.jsonl").write_text('{"ff1": 1}\n')

    compressed = dandi_cache.compress_derivatives(tmp_path)

    assert [path.name for path in compressed] == ["cache.jsonl.gz"]
    assert gzip.decompress((derivatives / "cache.jsonl.gz").read_bytes()) == b'{"ff1": 1}\n'
