"""Tests for the shared update loop.

These cover the behaviours that were subtly different in each repository before -- batch ordering,
what a failure means, and whether testing mode can touch the real cache -- because those are the
differences that caused real bugs.
"""

import dataclasses
import json
import math
import pathlib
import threading
import time

import pytest

import dandi_cache_utils as dandi_cache
from dandi_cache_utils import CacheDataset, _zarr


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


def _stored_records(dataset: CacheDataset, name: str | None = None) -> list:
    return [json.loads(line) for line in _zarr.iter_lines(dataset.output_store_path(name))]


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
    assert dataset.read_output_lookup() == {"a": 1, "b": 2, "c": 2}


@pytest.mark.ai_generated
@pytest.mark.parametrize("in_memory", [True, False])
def test_a_run_logs_how_much_of_its_candidates_is_recorded(dataset, caplog, in_memory):
    caplog.set_level("INFO")
    dandi_cache.run_incremental_update(
        dataset, candidates=["a", "b", "c", "d"], process=len, limit=1, in_memory=in_memory
    )
    dandi_cache.run_incremental_update(
        dataset, candidates=["a", "b", "c", "d"], process=len, limit=1, in_memory=in_memory
    )

    progress = [record.getMessage() for record in caplog.records if record.getMessage().startswith("Progress")]
    assert progress == [
        "Progress: 1 of 4 candidates recorded (25.0%).",
        "Progress: 2 of 4 candidates recorded (50.0%).",
    ]


@pytest.mark.ai_generated
def test_progress_can_be_logged_by_a_cache_that_selects_its_own_batch(caplog):
    caplog.set_level("INFO")
    dandi_cache.log_progress(975, 1252)
    dandi_cache.log_progress(0, 0)
    assert [record.getMessage() for record in caplog.records] == [
        "Progress: 975 of 1,252 candidates recorded (77.9%).",
        "Progress: 0 of 0 candidates recorded (100.0%).",
    ]


@pytest.mark.ai_generated
def test_a_limit_bounds_the_batch(dataset):
    _records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b", "c", "d"],
        process=lambda item: 1,
        limit=2,
    )

    assert result.considered == 2
    assert sorted(dataset.read_output_lookup()) == ["a", "b"]


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
def test_the_retries_start_where_the_rotation_says_and_wrap_around():
    recorded = {item: "timeout" for item in "abcde"}
    universe = ["f", *"edcba", "g"]

    def select(rotation, limit=None):
        return dandi_cache.select_new(
            universe, recorded, limit=limit, retry_when=lambda value: value == "timeout", rotation=rotation
        )

    assert select(0) == ["f", "g", "a", "b", "c", "d", "e"]
    assert select(2) == ["f", "g", "c", "d", "e", "a", "b"]
    assert select(7) == select(2)
    assert select(3, limit=4) == ["f", "g", "d", "e"]


@pytest.mark.ai_generated
def test_runs_that_stop_partway_through_the_retries_reach_every_one_of_them(dataset, monkeypatch):
    """A batch of retries cut short by the time limit used to retry the same first ones every run."""
    from dandi_cache_utils import _runner

    items = [f"id-{index}" for index in range(6)]
    dataset.write_output_lookup({item: {"status": "timeout"} for item in items})
    attempted = set()

    def process(item):
        attempted.add(item)
        raise TimeoutError("exceeded 20 min")

    for rotation in (0, 2, 4):
        monkeypatch.setattr(_runner, "_retry_rotation", lambda rotation=rotation: rotation)
        dandi_cache.run_incremental_update(
            dataset,
            candidates=items,
            process=process,
            limit=2,
            on_failure=dandi_cache.RETRY,
            failure_value=lambda item, scope: {"status": "timeout"},
            retry_when=lambda value: value["status"] == "timeout",
        )

    assert attempted == set(items)


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
    assert sorted(dataset.read_output_lookup()) == ["a", "b"]


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
def test_what_the_caller_adds_to_recorded_is_written_too(dataset):
    """A cache that fills in entries needing no processing, as a passthrough, has them published.

    The first run of `content-id-to-usage-dandiset-path` on EMBER handed over 6,053 such entries and
    8 to resolve, logged that it wrote 6,061, and published only the 8.
    """
    recorded = dataset.read_output_lookup()
    recorded.update({"unique-1": "x", "unique-2": "y"})

    dandi_cache.run_incremental_update(dataset, batch=["ambiguous"], process=lambda item: "z", recorded=recorded)

    assert dataset.read_output_lookup() == {"unique-1": "x", "unique-2": "y", "ambiguous": "z"}


@pytest.mark.ai_generated
def test_what_the_caller_drops_from_recorded_is_dropped_from_the_output(dataset):
    dataset.write_output_lookup({"gone": 1, "kept": 2})
    recorded = dataset.read_output_lookup()
    del recorded["gone"]

    dandi_cache.run_incremental_update(dataset, batch=["new"], process=lambda item: 3, recorded=recorded)

    assert dataset.read_output_lookup() == {"kept": 2, "new": 3}


@pytest.mark.ai_generated
def test_what_the_caller_changes_in_recorded_is_written_even_with_nothing_to_process(dataset):
    dataset.write_output_lookup({"a": 1})
    recorded = dataset.read_output_lookup()
    recorded["a"] = 2

    dandi_cache.run_incremental_update(dataset, batch=[], process=lambda item: 0, recorded=recorded)

    assert dataset.read_output_lookup() == {"a": 2}


@pytest.mark.ai_generated
def test_checkpoints_after_the_first_still_append_only_the_new_results(dataset):
    recorded = {"passthrough": 0}

    dandi_cache.run_incremental_update(
        dataset, batch=["a", "b", "c"], process=lambda item: item, recorded=recorded, checkpoint_every=1
    )

    store = dataset.output_store_path()
    assert [json.loads(line) for line in _zarr.iter_lines(store)] == [
        {"passthrough": 0},
        {"a": "a"},
        {"b": "b"},
        {"c": "c"},
    ]


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

    assert dataset.read_output_lookup("my_cache_checked_at.jsonl") == {item: "2026-09-15" for item in "abcd"}


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

    assert sorted(dataset.read_output_lookup()) == ["a", "b"]
    assert sorted(dataset.read_output_lookup("my_cache_checked_at.jsonl")) == ["a", "b"]


@pytest.mark.ai_generated
def test_testing_mode_never_touches_the_real_cache(tmp_path):
    cache_config = dandi_cache.parse_config({"cache": {"name": "my-cache"}, "inputs": []}, directory=tmp_path)
    real = CacheDataset(config=cache_config, base_directory=tmp_path)
    real.write_output_lookup({"already": "here"})

    testing = CacheDataset(config=cache_config, base_directory=tmp_path, testing=True)
    dandi_cache.run_incremental_update(testing, candidates=["a", "b"], process=lambda item: 1)

    assert real.read_output_lookup() == {"already": "here"}
    assert testing.output_file_path().name == "testing_my_cache.jsonl"
    assert sorted(testing.read_output_lookup()) == ["a", "b"]


@pytest.mark.ai_generated
def test_a_second_output_is_written_separately(dataset):
    dandi_cache.run_incremental_update(dataset, candidates=["a"], process=lambda item: True)
    dataset.write_output_lookup({"a": "2026-09-12"}, "my_cache_checked_at.jsonl")

    assert dataset.read_output_lookup() == {"a": True}
    assert dataset.read_output_lookup("my_cache_checked_at.jsonl") == {"a": "2026-09-12"}


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
    assert _stored_records(dataset) == [{"a": 1}, {"b": 2}]


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
    assert len(_stored_records(dataset)) == 3


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
def two_outputs(tmp_path) -> CacheDataset:
    cache_config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache", "outputs": ["my_cache.jsonl", "my_cache_messages.jsonl"]}},
        directory=tmp_path,
    )
    return CacheDataset(config=cache_config, base_directory=tmp_path)


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("format", "expected"),
    [
        ("lookup", {"0a": 1, "f1": 2}),
        ("records", [{"0a": 1}, {"f1": 2}]),
    ],
)
def test_an_upstream_kept_as_split_files_is_read_as_one_input(tmp_path, format, expected):
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


@pytest.mark.ai_generated
def test_an_upstream_split_into_4096_files_is_read_as_one_input(tmp_path):
    cache_config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache"}, "inputs": [{"name": "up-stream"}]}, directory=tmp_path
    )
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path)
    single = dataset.input_file_path()
    single.parent.mkdir(parents=True)
    dandi_cache.write_lookup(single.with_name("up_stream_0a1.jsonl"), {"0a1-x": 1})
    dandi_cache.write_lookup(single.with_name("up_stream_ff2.jsonl"), {"ff2-y": 2})

    assert dataset.read_input() == {"0a1-x": 1, "ff2-y": 2}


@pytest.mark.ai_generated
def test_an_upstream_split_into_256_files_is_read_as_one_input(tmp_path):
    cache_config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache"}, "inputs": [{"name": "up-stream"}]}, directory=tmp_path
    )
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path)
    single = dataset.input_file_path()
    single.parent.mkdir(parents=True)
    dandi_cache.write_lookup(single.with_name("up_stream_0a.jsonl"), {"0a1": 1})
    dandi_cache.write_lookup(single.with_name("up_stream_ff.jsonl"), {"ff2": 2})

    assert dataset.read_input() == {"0a1": 1, "ff2": 2}


@pytest.mark.ai_generated
def test_items_processed_concurrently_are_recorded_like_serial_ones(tmp_path, dataset):
    import threading

    running, peak, lock = [0], [0], threading.Lock()

    def process(item):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        try:
            import time

            time.sleep(0.05)
            if item == "c":
                raise RuntimeError("boom")
            return item.upper()
        finally:
            with lock:
                running[0] -= 1

    records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=list("abcdefgh"),
        process=process,
        on_failure=dandi_cache.RECORD,
        failure_value="failed",
        workers=4,
        checkpoint_every=3,
    )

    assert peak[0] > 1
    assert records == {**{item: item.upper() for item in "abdefgh"}, "c": "failed"}
    assert (result.considered, result.succeeded, result.failed) == (8, 7, 1)
    assert dataset.read_output_lookup() == records
    assert "boom" in (dataset.logs_directory / "unexpected_errors.txt").read_text()


@pytest.mark.ai_generated
def test_an_output_streams_one_item_at_a_time(two_outputs):
    two_outputs.write_output_lookup({"0a": 1, "fb": 2}, "my_cache_messages.jsonl")

    assert dict(two_outputs.iter_output("my_cache_messages.jsonl")) == {"0a": 1, "fb": 2}
    assert list(two_outputs.iter_output("my_cache.jsonl")) == []


@pytest.mark.ai_generated
def test_an_upstream_kept_as_split_files_streams_one_item_at_a_time(tmp_path):
    cache_config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache"}, "inputs": [{"name": "up-stream", "format": "lookup"}]},
        directory=tmp_path,
    )
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path)
    single = dataset.input_file_path()
    single.parent.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        list(dataset.iter_input())
    for prefix in "0123456789abcdef":
        dandi_cache.write_records(single.with_name(f"up_stream_{prefix}.jsonl"), [])
    dandi_cache.write_records(single.with_name("up_stream_0.jsonl"), [{"0a": 1}])
    dandi_cache.write_records(single.with_name("up_stream_f.jsonl"), [{"f1": 2}])

    assert list(dataset.iter_input()) == [("0a", 1), ("f1", 2)]


@pytest.mark.ai_generated
def test_merging_into_an_output_kept_as_a_file_moves_it_into_its_store(two_outputs):
    single = two_outputs.derivatives_directory / "my_cache_messages.jsonl"
    dandi_cache.write_lookup(single, {"0a": 1, "fb": 2})

    two_outputs.merge_output_lookup({"c1": 3}, "my_cache_messages.jsonl")

    assert not single.exists()
    assert two_outputs.output_store_path("my_cache_messages.jsonl").is_dir()
    assert two_outputs.read_output_lookup("my_cache_messages.jsonl") == {"0a": 1, "fb": 2, "c1": 3}


@pytest.mark.ai_generated
def test_an_update_out_of_memory_returns_only_its_batch_and_keeps_the_rest(two_outputs):
    two_outputs.write_output_lookup({"0a": 1, "fb": "retry"}, "my_cache_messages.jsonl")

    records, result = dandi_cache.run_incremental_update(
        two_outputs,
        candidates=["0a", "fb", "c1", "c2", "d3"],
        process=lambda item: f"done {item}",
        output="my_cache_messages.jsonl",
        on_failure=dandi_cache.RETRY,
        retry_when=lambda value: value == "retry",
        checkpoint_every=2,
        in_memory=False,
    )

    assert records == {"fb": "done fb", "c1": "done c1", "c2": "done c2", "d3": "done d3"}
    assert result.succeeded == 4
    assert two_outputs.read_output_lookup("my_cache_messages.jsonl") == {"0a": 1, **records}


@pytest.mark.ai_generated
def test_an_update_out_of_memory_takes_no_preloaded_records(two_outputs):
    with pytest.raises(ValueError, match="in_memory"):
        dandi_cache.run_incremental_update(
            two_outputs,
            candidates=["0a"],
            process=lambda item: 1,
            output="my_cache_messages.jsonl",
            recorded={},
            in_memory=False,
        )


@pytest.mark.ai_generated
def test_a_batch_that_outgrows_its_memory_limit_stops_early_and_keeps_what_it_did(dataset, monkeypatch):
    done = []
    monkeypatch.setattr("dandi_cache_utils._runner.peak_memory_mib", lambda: 10**6 if len(done) >= 3 else 1.0)

    def process(item):
        done.append(item)
        return True

    records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=[f"{i:02d}" for i in range(10)],
        process=process,
        memory_limit_mib=1000,
    )

    assert result.stopped_for_memory
    assert result.considered == 10
    assert sorted(records) == ["00", "01", "02"]
    assert dataset.read_output_lookup() == records


@pytest.mark.ai_generated
def test_a_batch_that_runs_past_its_time_limit_stops_early_and_keeps_what_it_did(dataset):
    records, result = dandi_cache.run_incremental_update(
        dataset,
        candidates=[f"{i:02d}" for i in range(10)],
        process=lambda item: True,
        time_limit_minutes=0,
    )

    assert result.stopped_for_time
    assert result.considered == 10
    assert sorted(records) == ["00"]
    assert dataset.read_output_lookup() == records


@pytest.mark.ai_generated
def test_an_item_that_never_finishes_is_abandoned_at_the_time_limit(dataset):
    """A hung read is abandoned, and what was done before it is kept.

    A batch of `valid-nwb-file-to-number-of-groups` hung for five hours inside one item, where a check
    between items never ran, and was killed with the job.
    """
    never = threading.Event()

    def process(item):
        if item == "b":
            never.wait()
        return item

    start = time.monotonic()
    records, result = dandi_cache.run_incremental_update(
        dataset, candidates=["a", "b", "c"], process=process, time_limit_minutes=0.02
    )

    assert time.monotonic() - start < 30
    assert result.stopped_for_time
    assert records == {"a": "a"}
    assert dataset.read_output_lookup() == {"a": "a"}


@pytest.mark.ai_generated
def test_an_abandoned_item_is_named_in_the_log(dataset, caplog):
    never = threading.Event()

    dandi_cache.run_incremental_update(
        dataset, candidates=["stuck"], process=lambda item: never.wait(), time_limit_minutes=0
    )

    assert "abandons it" in caplog.text
    assert "stuck" in caplog.text


@pytest.mark.ai_generated
def test_with_one_worker_the_next_item_waits_for_the_last_to_be_recorded(dataset):
    """Items run in a thread of their own, but never alongside the recording of the one before."""
    events = []

    def process(item):
        events.append(f"start {item}")
        return item

    dandi_cache.run_incremental_update(
        dataset,
        candidates=["a", "b", "c"],
        process=process,
        checkpoint_every=1,
        on_write=lambda: events.append("write"),
    )

    assert events[:6] == ["start a", "write", "start b", "write", "start c", "write"]


@pytest.mark.ai_generated
def test_a_batch_within_its_time_limit_runs_to_the_end(dataset):
    records, result = dandi_cache.run_incremental_update(
        dataset, candidates=["a", "b", "c"], process=lambda item: True, time_limit_minutes=math.inf
    )

    assert not result.stopped_for_time
    assert sorted(records) == ["a", "b", "c"]


@pytest.mark.ai_generated
def test_the_memory_limit_can_be_turned_off(dataset, monkeypatch):
    monkeypatch.setattr("dandi_cache_utils._runner.peak_memory_mib", lambda: 10**9)

    records, result = dandi_cache.run_incremental_update(
        dataset, candidates=["a", "b", "c"], process=lambda item: True, memory_limit_mib=math.inf
    )

    assert not result.stopped_for_memory
    assert sorted(records) == ["a", "b", "c"]


@pytest.mark.ai_generated
def test_the_default_memory_limit_is_a_share_of_what_the_machine_has():
    from dandi_cache_utils._logs import memory_limit_mib

    whole, half = memory_limit_mib(1.0), memory_limit_mib(0.5)

    assert whole is not None and whole > 0
    assert half == pytest.approx(whole / 2)
