"""Tests for the shared update loop.

These cover the behaviours that were subtly different in each repository before -- batch ordering,
what a failure means, and whether testing mode can touch the real cache -- because those are the
differences that caused real bugs.
"""

import dataclasses
import pathlib

import pytest

import dandi_cache_utils as dandi_cache
from dandi_cache_utils import CacheDataset


@pytest.fixture
def dataset(tmp_path) -> CacheDataset:
    cache_config = dandi_cache.parse_config(
        {
            "cache": {"name": "my-cache", "outputs": ["my_cache.jsonl", "my_cache_checked_at.jsonl"]},
            "inputs": [{"name": "up-stream"}],
        },
        directory=tmp_path,
    )
    return CacheDataset(config=cache_config, base_directory=tmp_path)


@pytest.mark.ai_generated
def test_the_frontier_is_sorted_and_capped():
    assert dandi_cache.select_new({"c", "a", "b", "d"}, {"b"}) == ["a", "c", "d"]
    assert dandi_cache.select_new({"c", "a", "b", "d"}, {"b"}, limit=2) == ["a", "c"]


@pytest.mark.ai_generated
def test_the_frontier_is_stable_across_calls():
    universe = {f"id-{index}" for index in range(50)}

    first = dandi_cache.select_new(universe, set(), limit=5)
    second = dandi_cache.select_new(set(universe), set(), limit=5)

    assert first == second


@pytest.mark.ai_generated
def test_stale_selection_takes_the_oldest_first():
    checked = {"a": "2026-01-01", "b": "2025-01-01", "c": "2026-06-01"}

    assert dandi_cache.select_stale(["a", "b", "c"], checked, limit=2) == ["b", "a"]


@pytest.mark.ai_generated
def test_stale_selection_puts_never_checked_items_first():
    assert dandi_cache.select_stale(["a", "b"], {"a": "2020-01-01"}, limit=1) == ["b"]


@pytest.mark.ai_generated
def test_stale_selection_sizes_the_batch_from_a_fraction():
    candidates = [f"id-{index:03d}" for index in range(100)]

    assert len(dandi_cache.select_stale(candidates, {}, fraction_per_run=1 / 30)) == 4


@pytest.mark.ai_generated
def test_the_dataset_resolves_the_limit_it_was_declared_with(tmp_path):
    cache_config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache"}, "operations": {"update": {"limit": 500, "testing_limit": 2}}},
        directory=tmp_path,
    )
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path)

    assert dataset.limit() == 500
    assert dataset.limit(50) == 50
    assert dataclasses.replace(dataset, testing=True).limit() == 2
    # Testing wins over an explicit override too: a smoke run is small whatever was asked for.
    assert dataclasses.replace(dataset, testing=True).limit(50) == 2


@pytest.mark.ai_generated
def test_the_limit_is_resolved_against_the_operation_being_run(tmp_path):
    cache_config = dandi_cache.parse_config(
        {
            "cache": {"name": "my-cache"},
            "operations": {"update": {"limit": 500}, "refresh": {"limit": 20, "testing_limit": 1}},
        },
        directory=tmp_path,
    )
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path, operation="refresh")

    assert dataset.limit() == 20
    assert dataclasses.replace(dataset, testing=True).limit() == 1


@pytest.mark.ai_generated
def test_a_cache_with_no_declared_limit_processes_everything(tmp_path):
    """Nothing declared means nothing to meter, and `--testing` then changes only where it writes.

    There is deliberately no library-side default to fall back on: the numbers live in
    `cache.toml`, so a cache that states none gets none rather than one it never chose.
    """
    cache_config = dandi_cache.parse_config({"cache": {"name": "my-cache"}}, directory=tmp_path)
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path)

    assert dataset.limit() is None
    assert dataclasses.replace(dataset, testing=True).limit() is None


@pytest.mark.ai_generated
def test_testing_without_a_testing_limit_runs_the_ordinary_batch(tmp_path):
    cache_config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache"}, "operations": {"update": {"limit": 500}}},
        directory=tmp_path,
    )
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path, testing=True)

    assert dataset.limit() == 500
    assert dataset.limit(50) == 50


@pytest.mark.ai_generated
def test_an_update_records_only_what_is_new(dataset):
    dataset.write_output_lookup({"a": 1})

    records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b", "c"],
        process=lambda item: len(item) + 1,
    )

    assert records == {"a": 1, "b": 2, "c": 2}
    assert (result.considered, result.succeeded, result.failed) == (2, 2, 0)
    assert dandi_cache.read_lookup(dataset.output_file_path()) == {"a": 1, "b": 2, "c": 2}


@pytest.mark.ai_generated
def test_a_limit_bounds_the_batch(dataset):
    _records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b", "c", "d"],
        process=lambda item: 1,
        limit=2,
    )

    assert result.considered == 2
    assert sorted(dandi_cache.read_lookup(dataset.output_file_path())) == ["a", "b"]


@pytest.mark.ai_generated
def test_a_skipped_failure_is_left_for_a_later_run(dataset):
    def process(item):
        if item == "b":
            raise RuntimeError("transient network read")
        return 1

    records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b", "c"],
        process=process,
        on_failure=dandi_cache.SKIP,
    )

    assert "b" not in records
    assert result.failed == 1
    assert records == {"a": 1, "c": 1}


@pytest.mark.ai_generated
def test_a_recorded_failure_is_never_retried(dataset):
    def process(item):
        if item == "b":
            raise RuntimeError("this file does not qualify")
        return True

    records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b", "c"],
        process=process,
        on_failure=dandi_cache.RECORD,
        failure_value=False,
    )

    assert records["b"] is False
    assert result.failed == 1

    _again, second = dandi_cache.run_incremental_update(dataset, candidates=["a", "b", "c"], process=process)
    assert second.considered == 0


@pytest.mark.ai_generated
def test_retryable_items_follow_the_untried_ones():
    recorded = {"a": "ok", "b": "timeout", "c": "not_hdf5", "e": "timeout"}

    batch = dandi_cache.select_new(
        ["e", "d", "c", "b", "a", "f"], recorded, retry_when=lambda value: value == "timeout"
    )

    assert batch == ["d", "f", "b", "e"]
    assert dandi_cache.select_new(["e", "d", "b"], recorded, limit=2, retry_when=lambda value: value == "timeout") == [
        "d",
        "b",
    ]


@pytest.mark.ai_generated
def test_a_retried_failure_is_published_and_selected_again(dataset):
    attempts = {"b": 0}

    def process(item):
        if item == "b":
            attempts["b"] += 1
            if attempts["b"] == 1:
                raise TimeoutError("exceeded 20 s")
        return {"status": "ok"}

    def failure(item, scope):
        return {"status": "timeout", "reason": str(scope.exception)}

    records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b"],
        process=process,
        on_failure=dandi_cache.RETRY,
        failure_value=failure,
        retry_when=lambda value: value["status"] == "timeout",
    )

    assert records["b"] == {"status": "timeout", "reason": "exceeded 20 s"}
    assert result.failed == 1
    assert result.succeeded == 1
    assert dataset.read_output_lookup()["b"] == {"status": "timeout", "reason": "exceeded 20 s"}

    again, second = dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b"],
        process=process,
        on_failure=dandi_cache.RETRY,
        failure_value=failure,
        retry_when=lambda value: value["status"] == "timeout",
    )

    assert second.considered == 1
    assert again["b"] == {"status": "ok"}


@pytest.mark.ai_generated
def test_a_recorded_failure_can_say_what_went_wrong(dataset):
    def process(item):
        raise ValueError(f"{item} is not an HDF5 file")

    records, _result = dandi_cache.run_incremental_update(
        dataset,
        candidates=["a"],
        process=process,
        on_failure=dandi_cache.RECORD,
        failure_value=lambda item, scope: {"reason": str(scope.exception)},
    )

    assert records == {"a": {"reason": "a is not an HDF5 file"}}


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("on_failure", "retry_when"),
    [(dandi_cache.RETRY, None), (dandi_cache.SKIP, bool), (dandi_cache.RECORD, bool)],
)
def test_retry_when_goes_with_the_retry_policy_only(dataset, on_failure, retry_when):
    with pytest.raises(ValueError, match="retry_when"):
        dandi_cache.run_incremental_update(
            dataset, candidates=["a"], process=lambda item: 1, on_failure=on_failure, retry_when=retry_when
        )


@pytest.mark.ai_generated
def test_a_failure_is_written_to_the_staged_error_log(dataset):
    def process(item, scope):
        scope.stage = "opening the NWB file"
        raise RuntimeError("boom")

    dandi_cache.run_incremental_update(
        dataset,
        candidates=["a"],
        process=process,
        stages={"opening the NWB file": "file_open_errors.txt"},
    )

    log_text = (dataset.logs_directory / "file_open_errors.txt").read_text()
    assert "opening the NWB file" in log_text
    assert "RuntimeError" in log_text


@pytest.mark.ai_generated
def test_an_unlabelled_failure_lands_in_the_catch_all(dataset):
    dandi_cache.run_incremental_update(
        dataset,
        candidates=["a"],
        process=lambda item: (_ for _ in ()).throw(RuntimeError("boom")),
        stages={"opening the NWB file": "file_open_errors.txt"},
    )

    assert (dataset.logs_directory / "unexpected_errors.txt").exists()


@pytest.mark.ai_generated
def test_nothing_records_nothing_without_failing(dataset):
    records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b"],
        process=lambda item: dandi_cache.NOTHING if item == "a" else 1,
    )

    assert records == {"b": 1}
    assert result.failed == 0


@pytest.mark.ai_generated
def test_a_keyboard_interrupt_stops_the_batch(dataset):
    def process(item):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        dandi_cache.run_incremental_update(dataset, candidates=["a"], process=process)


@pytest.mark.ai_generated
def test_checkpoints_survive_a_killed_run(dataset):
    processed = []

    def process(item):
        if item == "d":
            raise KeyboardInterrupt
        processed.append(item)
        return 1

    with pytest.raises(KeyboardInterrupt):
        dandi_cache.run_incremental_update(dataset, candidates=list("abcd"), process=process, checkpoint_every=2)

    assert processed == ["a", "b", "c"]
    assert sorted(dandi_cache.read_lookup(dataset.output_file_path())) == ["a", "b"]


@pytest.mark.ai_generated
def test_a_supplied_batch_re_assesses_what_is_already_recorded(dataset):
    dataset.write_output_lookup({"a": False, "b": False})

    records, result = dandi_cache.run_incremental_update(
        dataset,
        batch=["a"],
        process=lambda item: True,
    )

    assert records == {"a": True, "b": False}
    assert result.considered == 1


@pytest.mark.ai_generated
def test_the_batch_has_to_be_selected_exactly_one_way(dataset):
    with pytest.raises(ValueError, match="either `candidates`"):
        dandi_cache.run_incremental_update(dataset, process=lambda item: 1)

    with pytest.raises(ValueError, match="either `candidates`"):
        dandi_cache.run_incremental_update(dataset, candidates=["a"], batch=["a"], process=lambda item: 1)


@pytest.mark.ai_generated
def test_side_outputs_are_written_with_the_cache(dataset):
    checked_at = {}

    def process(item):
        checked_at[item] = "2026-09-15"
        return True

    dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b", "c", "d"],
        process=process,
        checkpoint_every=2,
        on_write=lambda: dataset.write_output_lookup(checked_at, "my_cache_checked_at.jsonl"),
    )

    assert dandi_cache.read_lookup(dataset.output_file_path("my_cache_checked_at.jsonl")) == {
        item: "2026-09-15" for item in "abcd"
    }


@pytest.mark.ai_generated
def test_side_outputs_keep_up_with_a_killed_run(dataset):
    checked_at = {}

    def process(item):
        if item == "c":
            raise KeyboardInterrupt
        checked_at[item] = "2026-09-15"
        return True

    with pytest.raises(KeyboardInterrupt):
        dandi_cache.run_incremental_update(
            dataset,
            candidates=list("abcd"),
            process=process,
            checkpoint_every=2,
            on_write=lambda: dataset.write_output_lookup(checked_at, "my_cache_checked_at.jsonl"),
        )

    assert sorted(dandi_cache.read_lookup(dataset.output_file_path())) == ["a", "b"]
    assert sorted(dandi_cache.read_lookup(dataset.output_file_path("my_cache_checked_at.jsonl"))) == ["a", "b"]


@pytest.mark.ai_generated
def test_testing_mode_never_touches_the_real_cache(tmp_path):
    cache_config = dandi_cache.parse_config({"cache": {"name": "my-cache"}, "inputs": []}, directory=tmp_path)
    real = CacheDataset(config=cache_config, base_directory=tmp_path)
    real.write_output_lookup({"already": "here"})

    testing = CacheDataset(config=cache_config, base_directory=tmp_path, testing=True)
    dandi_cache.run_incremental_update(testing, candidates=["a", "b"], process=lambda item: 1)

    assert dandi_cache.read_lookup(real.output_file_path()) == {"already": "here"}
    assert testing.output_file_path().name == "testing_my_cache.jsonl"
    assert sorted(dandi_cache.read_lookup(testing.output_file_path())) == ["a", "b"]


@pytest.mark.ai_generated
def test_a_second_output_is_written_separately(dataset):
    dandi_cache.run_incremental_update(dataset, candidates=["a"], process=lambda item: True)
    dataset.write_output_lookup({"a": "2026-09-12"}, "my_cache_checked_at.jsonl")

    assert dandi_cache.read_lookup(dataset.output_file_path()) == {"a": True}
    assert dandi_cache.read_lookup(dataset.output_file_path("my_cache_checked_at.jsonl")) == {"a": "2026-09-12"}


@pytest.mark.ai_generated
def test_an_undeclared_output_is_rejected(dataset):
    with pytest.raises(KeyError, match="not a declared output"):
        dataset.output_file_path("surprise.jsonl")


@pytest.mark.ai_generated
def test_a_full_rebuild_writes_a_record_list(tmp_path):
    cache_config = dandi_cache.parse_config({"cache": {"name": "my-cache"}}, directory=tmp_path)
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path)

    records, result = dandi_cache.run_full_rebuild(dataset, build=lambda: [{"a": 1}, {"b": 2}])

    assert records == [{"a": 1}, {"b": 2}]
    assert result.processed == 2
    assert dandi_cache.read_records(dataset.output_file_path()) == [{"a": 1}, {"b": 2}]


@pytest.mark.ai_generated
def test_a_full_rebuild_cannot_truncate_what_it_publishes(tmp_path):
    """A limit bounds work, not output, so there is nowhere in a rebuild to apply one.

    This is the footgun the parameter used to be: a cache that declared `limit = 500` published
    500 records and deleted the rest of itself from every consumer. Removing the parameter is what
    makes that unrepresentable, so the removal is what is pinned here.
    """
    cache_config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache"}, "operations": {"update": {"limit": 1}}},
        directory=tmp_path,
    )
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path)

    with pytest.raises(TypeError, match="limit"):
        dandi_cache.run_full_rebuild(dataset, build=lambda: [{"a": 1}], limit=1)

    # A declared limit is for `build` to apply to its own work; it never reaches the output.
    records, _result = dandi_cache.run_full_rebuild(dataset, build=lambda: [{"a": 1}, {"b": 2}, {"c": 3}])
    assert len(records) == 3
    assert len(dandi_cache.read_records(dataset.output_file_path())) == 3


@pytest.mark.ai_generated
def test_an_input_is_read_in_its_declared_shape(dataset, tmp_path):
    input_file = tmp_path / "sourcedata" / "up-stream" / "derivatives" / "up_stream.jsonl"
    input_file.parent.mkdir(parents=True)
    input_file.write_text('{"a": true}\n{"b": false}\n')

    assert dataset.read_input() == {"a": True, "b": False}


@pytest.mark.ai_generated
def test_a_missing_input_fails_loudly(dataset):
    with pytest.raises(FileNotFoundError):
        dataset.read_input()


@pytest.mark.ai_generated
def test_the_logs_directory_is_beside_the_derivatives(dataset, tmp_path):
    assert dataset.logs_directory == tmp_path / "logs"
    assert dataset.derivatives_directory == tmp_path / "derivatives"
    assert isinstance(dataset.base_directory, pathlib.Path)


@pytest.fixture
def split_dataset(tmp_path) -> CacheDataset:
    cache_config = dandi_cache.parse_config(
        {
            "cache": {
                "name": "my-cache",
                "outputs": ["my_cache.jsonl", "my_cache_messages.jsonl"],
                "split": ["my_cache_messages.jsonl"],
            },
        },
        directory=tmp_path,
    )
    return CacheDataset(config=cache_config, base_directory=tmp_path)


def _shard(dataset: CacheDataset, prefix: str, /) -> pathlib.Path:
    return dataset.derivatives_directory / f"{dataset.log_prefix}my_cache_messages_{prefix}.jsonl"


@pytest.mark.ai_generated
def test_a_split_output_is_written_by_first_digit_and_read_back_whole(split_dataset):
    records = {"0a": {"messages": ["x"]}, "0b": 1, "f9": [2], "A1": "upper case goes to the a file"}

    file_path = split_dataset.write_output_lookup(records, "my_cache_messages.jsonl")

    assert file_path == split_dataset.derivatives_directory / "my_cache_messages.jsonl"
    assert not file_path.exists()
    assert dandi_cache.read_lookup(_shard(split_dataset, "0")) == {"0a": {"messages": ["x"]}, "0b": 1}
    assert dandi_cache.read_lookup(_shard(split_dataset, "a")) == {"A1": "upper case goes to the a file"}
    # An empty file is still written, so the set of files never depends on the data.
    assert _shard(split_dataset, "5").read_text() == ""
    assert split_dataset.read_output_lookup("my_cache_messages.jsonl") == records


@pytest.mark.ai_generated
def test_a_split_output_refuses_a_key_with_no_hexadecimal_first_digit(split_dataset):
    with pytest.raises(ValueError, match="hexadecimal"):
        split_dataset.write_output_lookup({"zz": 1}, "my_cache_messages.jsonl")


@pytest.mark.ai_generated
def test_only_a_declared_output_can_be_split(tmp_path):
    with pytest.raises(ValueError, match="only an output can be split"):
        dandi_cache.parse_config({"cache": {"name": "my-cache", "split": ["other.jsonl"]}}, directory=tmp_path)


@pytest.mark.ai_generated
def test_an_update_of_a_newly_split_output_migrates_the_single_file_and_removes_it(split_dataset):
    single = split_dataset.derivatives_directory / "my_cache_messages.jsonl"
    dandi_cache.write_lookup(single, {"0a": 1, "fb": 2})

    records, result = dandi_cache.run_incremental_update(
        split_dataset,
        candidates=["0a", "fb", "c1"],
        process=lambda item: 3,
        output="my_cache_messages.jsonl",
    )

    assert result.considered == 1
    assert records == {"0a": 1, "fb": 2, "c1": 3}
    assert not single.exists()
    assert dandi_cache.read_lookup(_shard(split_dataset, "c")) == {"c1": 3}
    assert split_dataset.read_output_lookup("my_cache_messages.jsonl") == records


@pytest.mark.ai_generated
def test_undeclaring_a_split_joins_the_output_back_into_one_file(split_dataset, tmp_path):
    split_dataset.write_output_lookup({"0a": 1, "fb": 2}, "my_cache_messages.jsonl")
    unsplit = dataclasses.replace(split_dataset, config=dataclasses.replace(split_dataset.config, split=()))

    assert unsplit.read_output_lookup("my_cache_messages.jsonl") == {"0a": 1, "fb": 2}
    unsplit.write_output_lookup({"0a": 1, "fb": 2}, "my_cache_messages.jsonl")

    assert not any(_shard(split_dataset, prefix).exists() for prefix in "0123456789abcdef")
    assert dandi_cache.read_lookup(tmp_path / "derivatives" / "my_cache_messages.jsonl") == {"0a": 1, "fb": 2}


@pytest.mark.ai_generated
def test_a_split_output_in_testing_mode_never_touches_the_real_files(split_dataset):
    testing = dataclasses.replace(split_dataset, testing=True)

    testing.write_output_lookup({"0a": 1}, "my_cache_messages.jsonl")

    assert dandi_cache.read_lookup(_shard(testing, "0")) == {"0a": 1}
    assert not _shard(split_dataset, "0").exists()


@pytest.mark.ai_generated
def test_a_split_rebuild_writes_each_record_to_its_file(tmp_path):
    cache_config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache", "split": ["my_cache.jsonl"]}},
        directory=tmp_path,
    )
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path)

    dandi_cache.run_full_rebuild(dataset, build=lambda: [{"0a": 1}, {"f1": 2}, {"0b": 3}])

    assert dandi_cache.read_records(tmp_path / "derivatives" / "my_cache_0.jsonl") == [{"0a": 1}, {"0b": 3}]
    assert dandi_cache.read_records(tmp_path / "derivatives" / "my_cache_f.jsonl") == [{"f1": 2}]
    assert not (tmp_path / "derivatives" / "my_cache.jsonl").exists()


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("format", "expected"),
    [
        ("lookup", {"0a": 1, "f1": 2}),
        ("records", [{"0a": 1}, {"f1": 2}]),
    ],
)
def test_a_split_upstream_is_read_as_one_input(tmp_path, format, expected):
    cache_config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache"}, "inputs": [{"name": "up-stream", "format": format}]},
        directory=tmp_path,
    )
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path)
    single = dataset.input_file_path()
    single.parent.mkdir(parents=True)
    for prefix in "0123456789abcdef":
        dandi_cache.write_records(single.with_name(f"up_stream_{prefix}.jsonl"), [])
    dandi_cache.write_records(single.with_name("up_stream_0.jsonl"), [{"0a": 1}])
    dandi_cache.write_records(single.with_name("up_stream_f.jsonl"), [{"f1": 2}])

    assert dataset.read_input() == expected
