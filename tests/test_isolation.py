"""Tests for running one item's work in a child process under a timeout."""

import os
import time

import pytest

import dandi_cache_utils as dandi_cache


class _NeedsTwoArguments(Exception):
    """Pickles from `args` alone, so it cannot be rebuilt in the parent."""

    def __init__(self, status, reason):
        super().__init__(f"{status}: {reason}")


def _raise_unloadable():
    raise _NeedsTwoArguments("timeout", "slow")


@pytest.mark.ai_generated
def test_the_result_comes_back():
    assert dandi_cache.run_isolated(sorted, arguments=([3, 1, 2],), timeout_seconds=30) == [1, 2, 3]


@pytest.mark.ai_generated
def test_a_call_past_its_timeout_is_stopped():
    start = time.monotonic()

    with pytest.raises(TimeoutError):
        dandi_cache.run_isolated(time.sleep, arguments=(60,), timeout_seconds=0.5)

    assert time.monotonic() - start < 10


@pytest.mark.ai_generated
def test_an_exception_is_raised_as_it_was():
    with pytest.raises(KeyError, match="missing"):
        dandi_cache.run_isolated({}.__getitem__, arguments=("missing",), timeout_seconds=30)


@pytest.mark.ai_generated
def test_an_exception_that_cannot_cross_back_still_says_what_it_was():
    with pytest.raises(RuntimeError, match="_NeedsTwoArguments: timeout: slow"):
        dandi_cache.run_isolated(_raise_unloadable, timeout_seconds=30)


@pytest.mark.ai_generated
def test_a_child_that_dies_without_answering_reports_its_exit_code():
    with pytest.raises(ChildProcessError, match="code 3"):
        dandi_cache.run_isolated(os._exit, arguments=(3,), timeout_seconds=30)
