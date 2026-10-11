"""Tests for keeping an output as a Zarr store that only grows by appending."""

import gzip
import json
import pathlib

import pytest

import dandi_cache_utils as dandi_cache
from dandi_cache_utils import CacheDataset, _zarr

CHUNK = 1_000


def lines(start: int, count: int) -> list[str]:
    return [json.dumps({f"{index:06d}": f"A title number {index}"}) for index in range(start, start + count)]


def chunk_files(store: pathlib.Path) -> list[pathlib.Path]:
    return sorted((store / "stream" / "c").iterdir(), key=lambda path: int(path.name))


@pytest.mark.ai_generated
def test_appending_writes_text_chunks_and_leaves_earlier_chunks_alone(tmp_path):
    store = tmp_path / "out.zarr"
    _zarr.append_lines(store, lines(0, 100), chunk_bytes=CHUNK)
    first = {path.name: path.read_bytes() for path in chunk_files(store)}
    _zarr.append_lines(store, lines(100, 100), chunk_bytes=CHUNK)
    second = {path.name: path.read_bytes() for path in chunk_files(store)}

    assert len(second) > len(first)
    assert all(len(data) == CHUNK and b"\x00" not in data for data in second.values())
    last_before = max(first, key=int)
    assert all(second[name] == first[name] for name in first if name != last_before)
    assert list(_zarr.iter_lines(store)) == lines(0, 200)


@pytest.mark.ai_generated
def test_no_record_is_split_across_two_chunks(tmp_path):
    store = tmp_path / "out.zarr"
    records = [json.dumps({f"{index:03d}": "x" * (40 + index % 50)}) for index in range(60)]
    _zarr.append_lines(store, records[:30], chunk_bytes=300)
    _zarr.append_lines(store, records[30:], chunk_bytes=999_999)  # An existing store keeps its own size.

    assert len(chunk_files(store)) > 3
    found = []
    for path in chunk_files(store):
        found += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert found == [json.loads(record) for record in records]


@pytest.mark.ai_generated
def test_a_record_longer_than_a_chunk_is_refused(tmp_path):
    with pytest.raises(ValueError, match="does not fit"):
        _zarr.append_lines(tmp_path / "out.zarr", [json.dumps({"k": "x" * 200})], chunk_bytes=100)


@pytest.mark.ai_generated
def test_the_last_line_for_a_key_wins_and_compacting_drops_the_rest(tmp_path):
    store = tmp_path / "out.zarr"
    _zarr.append_lines(store, ['{"a": 1}', '{"b": 2}'], chunk_bytes=CHUNK)
    _zarr.append_lines(store, ['{"a": 3}'], chunk_bytes=CHUNK)

    assert list(_zarr.iter_latest_lines(store)) == ['{"b": 2}', '{"a": 3}']
    assert _zarr.count_lines(store) == (3, 2)
    _zarr.compact(store)
    assert list(_zarr.iter_lines(store)) == ['{"b": 2}', '{"a": 3}']


@pytest.mark.ai_generated
def test_a_key_needing_unescaping_is_still_found(tmp_path):
    store = tmp_path / "out.zarr"
    _zarr.append_lines(store, [json.dumps({"é": 1}), json.dumps({"é": 2})], chunk_bytes=CHUNK)

    assert [json.loads(line) for line in _zarr.iter_latest_lines(store)] == [{"é": 2}]


@pytest.mark.ai_generated
def test_the_metadata_is_consolidated_and_tracks_the_length(tmp_path):
    store = tmp_path / "out.zarr"
    for start in (0, 50):
        _zarr.append_lines(store, lines(start, 50), chunk_bytes=CHUNK)
        root = json.loads((store / "zarr.json").read_text())
        array = json.loads((store / "stream" / "zarr.json").read_text())
        assert root["node_type"] == "group"
        assert root["consolidated_metadata"]["metadata"]["stream"] == array
        assert array["shape"][0] > 0
    assert array["codecs"] == [{"name": "bytes"}]
    assert array["fill_value"] == 10


@pytest.mark.ai_generated
def test_a_written_store_is_read_back_by_the_zarr_library(tmp_path):
    zarr = pytest.importorskip("zarr")
    if int(zarr.__version__.split(".")[0]) < 3:
        pytest.skip("Needs zarr 3, which reads the version 3 format.")
    store = tmp_path / "out.zarr"
    _zarr.append_lines(store, lines(0, 100), chunk_bytes=CHUNK)
    _zarr.append_lines(store, lines(100, 50), chunk_bytes=CHUNK)

    array = zarr.open_group(store, mode="r", use_consolidated=True)["stream"]

    text = array[:].tobytes().decode("utf-8")
    assert [line for line in text.split("\n") if line.strip()] == lines(0, 150)

    published = tmp_path / "published.zarr"
    _zarr.write_fresh(published, lines(0, 150), chunk_bytes=CHUNK, compressed=True)
    compressed = zarr.open_group(published, mode="r", use_consolidated=True)["stream"]
    published_text = compressed[:].tobytes().decode("utf-8")
    assert [line for line in published_text.split("\n") if line.strip()] == lines(0, 150)
    assert list(_zarr.iter_lines(published)) == lines(0, 150)


@pytest.mark.ai_generated
def test_writing_afresh_replaces_the_store_and_compressed_chunks_are_reproducible(tmp_path):
    first = tmp_path / "a.zarr"
    second = tmp_path / "b.zarr"
    _zarr.write_fresh(first, lines(0, 100), chunk_bytes=CHUNK, compressed=True)
    _zarr.write_fresh(second, lines(0, 100), chunk_bytes=CHUNK, compressed=True)

    assert [path.read_bytes() for path in chunk_files(first)] == [path.read_bytes() for path in chunk_files(second)]
    assert gzip.decompress(chunk_files(first)[0].read_bytes()).startswith(lines(0, 1)[0].encode())
    _zarr.write_fresh(first, lines(0, 3), chunk_bytes=CHUNK)
    assert list(_zarr.iter_lines(first)) == lines(0, 3)
    assert not list(tmp_path.glob("*.writing"))


def zarr_dataset(tmp_path, **cache) -> CacheDataset:
    config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache", "zarr": ["my_cache.jsonl"], "zarr_chunk_bytes": CHUNK, **cache}},
        directory=tmp_path,
    )
    return CacheDataset(config=config, base_directory=tmp_path)


@pytest.mark.ai_generated
def test_an_output_declared_zarr_is_merged_read_and_iterated_through_the_dataset(tmp_path):
    dataset = zarr_dataset(tmp_path)

    dataset.merge_output_lookup({"a": 1, "b": 2})
    dataset.merge_output_lookup({"a": 3, "c": 4})

    derivatives = tmp_path / "derivatives"
    assert (derivatives / "my_cache.zarr" / "zarr.json").exists()
    assert not (derivatives / "my_cache.jsonl").exists()
    assert dataset.read_output_lookup() == {"a": 3, "b": 2, "c": 4}
    assert dict(dataset.iter_output()) == {"a": 3, "b": 2, "c": 4}


@pytest.mark.ai_generated
def test_writing_a_whole_lookup_appends_only_what_changed(tmp_path):
    dataset = zarr_dataset(tmp_path)
    dataset.write_output_lookup({"a": 1, "b": 2})
    store = tmp_path / "derivatives" / "my_cache.zarr"
    before = list(_zarr.iter_lines(store))

    dataset.write_output_lookup({"a": 1, "b": 5, "c": 6})

    assert list(_zarr.iter_lines(store))[: len(before)] == before
    assert list(_zarr.iter_lines(store))[len(before) :] == ['{"b": 5}', '{"c": 6}']
    assert dataset.read_output_lookup() == {"a": 1, "b": 5, "c": 6}

    dataset.write_output_lookup({"a": 1})
    assert dataset.read_output_lookup() == {"a": 1}
    assert list(_zarr.iter_lines(store)) == ['{"a": 1}']


@pytest.mark.ai_generated
def test_writing_records_appends_when_the_store_holds_their_beginning(tmp_path):
    dataset = zarr_dataset(tmp_path)
    dataset.write_output_records([{"a": 1}, {"b": 2}])
    dataset.write_output_records([{"a": 1}, {"b": 2}, {"c": 3}])
    store = tmp_path / "derivatives" / "my_cache.zarr"
    assert list(_zarr.iter_lines(store)) == ['{"a": 1}', '{"b": 2}', '{"c": 3}']

    dataset.write_output_records([{"z": 0}])
    assert list(_zarr.iter_lines(store)) == ['{"z": 0}']


@pytest.mark.ai_generated
@pytest.mark.parametrize("before", [None, 16, 256, 4096])
def test_declaring_zarr_moves_what_was_kept_as_files_into_the_store(tmp_path, before):
    entries = {"0a1-x": 1, "ff3-y": 2, "123-z": 3}
    if before is None:
        older = dandi_cache.parse_config({"cache": {"name": "my-cache"}}, directory=tmp_path)
    else:
        digits = {16: 1, 256: 2, 4096: 3}[before]
        older = dandi_cache.parse_config(
            {"cache": {"name": "my-cache", "split": ["my_cache.jsonl"], "split_digits": digits}}, directory=tmp_path
        )
    CacheDataset(config=older, base_directory=tmp_path).write_output_lookup(entries)

    dataset = zarr_dataset(tmp_path)
    assert dataset.read_output_lookup() == entries
    dataset.merge_output_lookup({"new-1": 4})

    derivatives = tmp_path / "derivatives"
    assert sorted(path.name for path in derivatives.iterdir()) == ["my_cache.zarr"]
    assert dataset.read_output_lookup() == {**entries, "new-1": 4}


@pytest.mark.ai_generated
def test_dropping_zarr_moves_the_store_back_into_files(tmp_path):
    dataset = zarr_dataset(tmp_path)
    dataset.merge_output_lookup({"0a1-x": 1})
    dataset.merge_output_lookup({"0a1-x": 2, "ff3-y": 3})
    plain = CacheDataset(
        config=dandi_cache.parse_config({"cache": {"name": "my-cache"}}, directory=tmp_path), base_directory=tmp_path
    )

    assert plain.read_output_lookup() == {"0a1-x": 2, "ff3-y": 3}
    plain.write_output_lookup(plain.read_output_lookup())

    derivatives = tmp_path / "derivatives"
    assert sorted(path.name for path in derivatives.iterdir()) == ["my_cache.jsonl"]
    assert dandi_cache.read_lookup(derivatives / "my_cache.jsonl") == {"0a1-x": 2, "ff3-y": 3}


@pytest.mark.ai_generated
def test_a_store_is_compacted_once_enough_of_it_is_superseded(tmp_path):
    dataset = zarr_dataset(tmp_path)
    dataset.merge_output_lookup({f"k{index:04d}": index for index in range(30)})
    store = tmp_path / "derivatives" / "my_cache.zarr"

    dataset.merge_output_lookup({f"k{index:04d}": -index for index in range(3)})
    assert not dataset.compact_output_if_superseded(fraction=0.2, minimum=1)

    dataset.merge_output_lookup({f"k{index:04d}": -index for index in range(3, 20)})
    assert dataset.compact_output_if_superseded(fraction=0.2, minimum=1)

    assert _zarr.count_lines(store) == (30, 30)
    assert dataset.read_output_lookup()["k0010"] == -10
    assert not dataset.compact_output()


@pytest.mark.ai_generated
def test_a_downstream_cache_reads_an_upstream_kept_as_a_store(tmp_path):
    config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache"}, "inputs": [{"name": "up-stream"}]}, directory=tmp_path
    )
    dataset = CacheDataset(config=config, base_directory=tmp_path)
    single = dataset.input_file_path()
    store = _zarr.store_path(single)
    _zarr.append_lines(store, ['{"a": 1}', '{"b": 2}', '{"a": 3}'], chunk_bytes=CHUNK)

    assert dataset.read_input() == {"a": 3, "b": 2}
    assert dict(dataset.iter_input()) == {"a": 3, "b": 2}


@pytest.mark.ai_generated
@pytest.mark.parametrize("in_memory", [True, False])
def test_an_incremental_update_appends_to_a_zarr_output(tmp_path, in_memory):
    dataset = zarr_dataset(tmp_path)
    dataset.merge_output_lookup({"a": "old", "b": "old"})
    store = tmp_path / "derivatives" / "my_cache.zarr"
    before = list(_zarr.iter_lines(store))

    records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b", "c", "d"],
        process=lambda item: item.upper(),
        in_memory=in_memory,
    )

    assert result.succeeded == 2
    assert list(_zarr.iter_lines(store))[: len(before)] == before
    assert dataset.read_output_lookup() == {"a": "old", "b": "old", "c": "C", "d": "D"}


@pytest.mark.ai_generated
def test_compressing_publishes_a_compacted_compressed_store_beside_one_joined_file(tmp_path):
    from click.testing import CliRunner

    from dandi_cache_utils._cli import dandi_cache_cli

    dataset = zarr_dataset(tmp_path)
    dataset.merge_output_lookup({"a": 1, "b": 2})
    dataset.merge_output_lookup({"a": 3})
    config_file = tmp_path / "cache.toml"
    config_file.write_text('[cache]\nname = "my-cache"\nzarr = ["my_cache.jsonl"]\nzarr_chunk_bytes = 1000\n')
    dist = tmp_path / "dist"

    result = CliRunner().invoke(
        dandi_cache_cli,
        ["compress", "--base-directory", str(tmp_path), "--config", str(config_file), "--dist-directory", str(dist)],
    )

    assert result.exit_code == 0, result.output
    published = dist / "derivatives"
    assert list(_zarr.iter_lines(published / "my_cache.zarr")) == ['{"b": 2}', '{"a": 3}']
    array = json.loads((published / "my_cache.zarr" / "stream" / "zarr.json").read_text())
    assert [codec["name"] for codec in array["codecs"]] == ["bytes", "gzip"]
    assert gzip.decompress((published / "my_cache.jsonl.gz").read_bytes()) == b'{"b": 2}\n{"a": 3}\n'


@pytest.mark.ai_generated
def test_zarr_settings_are_checked(tmp_path):
    with pytest.raises(ValueError, match="not one of `cache.outputs`"):
        dandi_cache.parse_config({"cache": {"name": "my-cache", "zarr": ["other.jsonl"]}}, directory=tmp_path)
    with pytest.raises(ValueError, match="both `cache.zarr` and `cache.split`"):
        dandi_cache.parse_config(
            {"cache": {"name": "my-cache", "zarr": ["my_cache.jsonl"], "split": ["my_cache.jsonl"]}},
            directory=tmp_path,
        )
    with pytest.raises(ValueError, match="describes `cache.zarr`"):
        dandi_cache.parse_config({"cache": {"name": "my-cache", "zarr_chunk_bytes": 5_000_000}}, directory=tmp_path)
    with pytest.raises(ValueError, match="limit"):
        dandi_cache.parse_config(
            {"cache": {"name": "my-cache", "zarr": ["my_cache.jsonl"], "zarr_chunk_bytes": 10**9}}, directory=tmp_path
        )
    config = dandi_cache.parse_config({"cache": {"name": "my-cache", "zarr": ["my_cache.jsonl"]}}, directory=tmp_path)
    assert config.zarr_chunk_bytes == 80_000_000
    assert config.dist_files == ()
