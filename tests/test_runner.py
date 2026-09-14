"""Tests for the shared update loop.

These cover the behaviours that were subtly different in each repository before -- batch ordering,
what a failure means, and whether testing mode can touch the real cache -- because those are the
differences that caused real bugs.
"""

import pathlib

import pytest

from dandi_cache_utils import CacheDataset, config, jsonl, runner


@pytest.fixture
def dataset(tmp_path) -> CacheDataset:
    cache_config = config.parse_config(
        {
            "cache": {"name": "my-cache", "outputs": ["my_cache.jsonl", "my_cache_checked_at.jsonl"]},
            "inputs": [{"name": "up-stream"}],
        },
        directory=tmp_path,
    )
    return CacheDataset(config=cache_config, base_directory=tmp_path)


@pytest.mark.ai_generated
def test_the_frontier_is_sorted_and_capped():
    assert runner.select_new({"c", "a", "b", "d"}, {"b"}) == ["a", "c", "d"]
    assert runner.select_new({"c", "a", "b", "d"}, {"b"}, limit=2) == ["a", "c"]


@pytest.mark.ai_generated
def test_the_frontier_is_stable_across_calls():
    universe = {f"id-{index}" for index in range(50)}

    first = runner.select_new(universe, set(), limit=5)
    second = runner.select_new(set(universe), set(), limit=5)

    assert first == second


@pytest.mark.ai_generated
def test_stale_selection_takes_the_oldest_first():
    checked = {"a": "2026-01-01", "b": "2025-01-01", "c": "2026-06-01"}

    assert runner.select_stale(["a", "b", "c"], checked, limit=2) == ["b", "a"]


@pytest.mark.ai_generated
def test_stale_selection_puts_never_checked_items_first():
    assert runner.select_stale(["a", "b"], {"a": "2020-01-01"}, limit=1) == ["b"]


@pytest.mark.ai_generated
def test_stale_selection_sizes_the_batch_from_a_fraction():
    candidates = [f"id-{index:03d}" for index in range(100)]

    assert len(runner.select_stale(candidates, {}, fraction_per_run=1 / 30)) == 4


@pytest.mark.ai_generated
def test_testing_always_wins_on_batch_size():
    assert runner.effective_limit(testing=True, limit=5000, default=500) == 10
    assert runner.effective_limit(testing=False, limit=5000, default=500) == 5000
    assert runner.effective_limit(testing=False, limit=None, default=500) == 500
    assert runner.effective_limit(testing=False, limit=None, default=None) is None


@pytest.mark.ai_generated
def test_an_update_records_only_what_is_new(dataset):
    dataset.write_output_lookup({"a": 1})

    records, result = runner.run_incremental_update(
        dataset,
        candidates=["a", "b", "c"],
        process=lambda item: len(item) + 1,
    )

    assert records == {"a": 1, "b": 2, "c": 2}
    assert (result.considered, result.succeeded, result.failed) == (2, 2, 0)
    assert jsonl.read_lookup(dataset.output_file_path()) == {"a": 1, "b": 2, "c": 2}


@pytest.mark.ai_generated
def test_a_limit_bounds_the_batch(dataset):
    _records, result = runner.run_incremental_update(
        dataset,
        candidates=["a", "b", "c", "d"],
        process=lambda item: 1,
        limit=2,
    )

    assert result.considered == 2
    assert sorted(jsonl.read_lookup(dataset.output_file_path())) == ["a", "b"]


@pytest.mark.ai_generated
def test_a_skipped_failure_is_left_for_a_later_run(dataset):
    def process(item):
        if item == "b":
            raise RuntimeError("transient network read")
        return 1

    records, result = runner.run_incremental_update(
        dataset,
        candidates=["a", "b", "c"],
        process=process,
        on_failure=runner.SKIP,
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

    records, result = runner.run_incremental_update(
        dataset,
        candidates=["a", "b", "c"],
        process=process,
        on_failure=runner.RECORD,
        failure_value=False,
    )

    assert records["b"] is False
    assert result.failed == 1

    _again, second = runner.run_incremental_update(dataset, candidates=["a", "b", "c"], process=process)
    assert second.considered == 0


@pytest.mark.ai_generated
def test_a_failure_is_written_to_the_staged_error_log(dataset):
    def process(item, scope):
        scope.stage = "opening the NWB file"
        raise RuntimeError("boom")

    runner.run_incremental_update(
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
    runner.run_incremental_update(
        dataset,
        candidates=["a"],
        process=lambda item: (_ for _ in ()).throw(RuntimeError("boom")),
        stages={"opening the NWB file": "file_open_errors.txt"},
    )

    assert (dataset.logs_directory / "unexpected_errors.txt").exists()


@pytest.mark.ai_generated
def test_nothing_records_nothing_without_failing(dataset):
    records, result = runner.run_incremental_update(
        dataset,
        candidates=["a", "b"],
        process=lambda item: runner.NOTHING if item == "a" else 1,
    )

    assert records == {"b": 1}
    assert result.failed == 0


@pytest.mark.ai_generated
def test_a_keyboard_interrupt_stops_the_batch(dataset):
    def process(item):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        runner.run_incremental_update(dataset, candidates=["a"], process=process)


@pytest.mark.ai_generated
def test_checkpoints_survive_a_killed_run(dataset):
    processed = []

    def process(item):
        if item == "d":
            raise KeyboardInterrupt
        processed.append(item)
        return 1

    with pytest.raises(KeyboardInterrupt):
        runner.run_incremental_update(dataset, candidates=list("abcd"), process=process, checkpoint_every=2)

    assert processed == ["a", "b", "c"]
    assert sorted(jsonl.read_lookup(dataset.output_file_path())) == ["a", "b"]


@pytest.mark.ai_generated
def test_testing_mode_never_touches_the_real_cache(tmp_path):
    cache_config = config.parse_config({"cache": {"name": "my-cache"}, "inputs": []}, directory=tmp_path)
    real = CacheDataset(config=cache_config, base_directory=tmp_path)
    real.write_output_lookup({"already": "here"})

    testing = CacheDataset(config=cache_config, base_directory=tmp_path, testing=True)
    runner.run_incremental_update(testing, candidates=["a", "b"], process=lambda item: 1)

    assert jsonl.read_lookup(real.output_file_path()) == {"already": "here"}
    assert testing.output_file_path().name == "testing_my_cache.jsonl"
    assert sorted(jsonl.read_lookup(testing.output_file_path())) == ["a", "b"]


@pytest.mark.ai_generated
def test_a_second_output_is_written_separately(dataset):
    runner.run_incremental_update(dataset, candidates=["a"], process=lambda item: True)
    dataset.write_output_lookup({"a": "2026-09-12"}, "my_cache_checked_at.jsonl")

    assert jsonl.read_lookup(dataset.output_file_path()) == {"a": True}
    assert jsonl.read_lookup(dataset.output_file_path("my_cache_checked_at.jsonl")) == {"a": "2026-09-12"}


@pytest.mark.ai_generated
def test_an_undeclared_output_is_rejected(dataset):
    with pytest.raises(KeyError, match="not a declared output"):
        dataset.output_file_path("surprise.jsonl")


@pytest.mark.ai_generated
def test_a_full_rebuild_writes_a_record_list(tmp_path):
    cache_config = config.parse_config({"cache": {"name": "my-cache"}}, directory=tmp_path)
    dataset = CacheDataset(config=cache_config, base_directory=tmp_path)

    records, result = runner.run_full_rebuild(dataset, build=lambda: [{"a": 1}, {"b": 2}])

    assert records == [{"a": 1}, {"b": 2}]
    assert result.processed == 2
    assert jsonl.read_records(dataset.output_file_path()) == [{"a": 1}, {"b": 2}]


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
