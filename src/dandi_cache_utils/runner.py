"""The update loop that every cache runs, minus the one operation that makes it different.

Each cache does the same six things: work out which items are not yet recorded, cap the batch,
process each item, log a line about it, decide what a failure means, and write the result out.
Only the per-item operation differs. `run_incremental_update` is that loop; the cache supplies
the operation.

Two failure policies are in use across the organization and both are supported explicitly,
because choosing the wrong one is a real bug:

- `skip` leaves a failed item unrecorded so a later run retries it. Correct when the work is
  known to be possible and a failure is almost always transient, e.g. a network read of a file
  that upstream already opened successfully.
- `record` writes `failure_value` for the item so it is never retried. Correct when the failure
  *is* the answer, e.g. a file that cannot be validated does not qualify.
"""

import dataclasses
import inspect
import itertools
import math
import time
import typing

from .dataset import TESTING_LIMIT, CacheDataset
from .logs import StagedErrorLog, logger, peak_memory_mib

SKIP = "skip"
RECORD = "record"

#: Returned by an operation to mean "there is nothing to record for this item".
NOTHING = object()


@dataclasses.dataclass
class BatchResult:
    """What one batch did, for the summary line and for tests."""

    considered: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0

    @property
    def elapsed_minutes(self) -> float:
        """Wall-clock minutes the batch took."""
        return self.elapsed_seconds / 60


def select_new(
    universe: typing.Iterable,
    recorded: typing.Container,
    /,
    *,
    limit: int | None = None,
) -> list:
    """The items in `universe` not already recorded, sorted, capped at `limit`.

    Sorting matters: iterating a `set` gives a different batch on every run, so a failing item can
    silently rotate in and out of the batch instead of being seen and fixed.
    """
    frontier = sorted(item for item in universe if item not in recorded)
    return frontier if limit is None else list(itertools.islice(frontier, limit))


def select_stale(
    candidates: typing.Iterable,
    last_checked: typing.Mapping,
    /,
    *,
    limit: int | None = None,
    fraction_per_run: float | None = None,
    missing_sentinel: str = "0000-00-00",
) -> list:
    """The already-recorded items most overdue for re-assessment, oldest first.

    A cache whose answer depends on an evolving external tool (an NWB validator, say) has to
    re-check what it already recorded. With `fraction_per_run` set and no explicit `limit`, the
    batch is sized so that running once per period cycles the whole cache over that many periods.
    """
    ordered = sorted(candidates, key=lambda item: (last_checked.get(item, missing_sentinel), item))
    if limit is None and fraction_per_run is not None:
        limit = max(1, math.ceil(len(ordered) * fraction_per_run)) if ordered else 0
    return ordered if limit is None else list(itertools.islice(ordered, limit))


def effective_limit(*, testing: bool, limit: int | None, default: int | None = None) -> int | None:
    """Resolve the batch cap from the testing flag, an explicit limit, and the configured default.

    Testing always wins: a smoke run is meant to be small and fast regardless of what the cache's
    ordinary batch size is.
    """
    if testing:
        return TESTING_LIMIT
    return limit if limit is not None else default


def run_incremental_update(
    dataset: CacheDataset,
    /,
    *,
    candidates: typing.Iterable,
    process: typing.Callable[..., typing.Any],
    limit: int | None = None,
    output: str | None = None,
    recorded: typing.MutableMapping | None = None,
    on_failure: str = SKIP,
    failure_value: typing.Any = False,
    on_error: typing.Callable[[typing.Any, typing.Any], None] | None = None,
    describe: typing.Callable[[typing.Any], str] | None = None,
    stages: typing.Mapping[str, str] | None = None,
    checkpoint_every: int | None = None,
    write: bool = True,
) -> tuple[dict, BatchResult]:
    """Process the not-yet-recorded candidates and write the updated cache.

    `process` is called with the item, and with the per-item error scope as a second argument when
    it accepts one -- setting `item.stage` on that scope is what routes a failure to the right
    error log. Returning `NOTHING` records nothing for the item without counting as a failure.
    `on_error(item, scope)` is called for each failure, for caches that keep side outputs about
    why an item failed.

    Returns the full `{item: value}` mapping and a `BatchResult` describing the batch.
    """
    if on_failure not in (SKIP, RECORD):
        raise ValueError(f"on_failure must be {SKIP!r} or {RECORD!r}, got {on_failure!r}.")

    records = dataset.read_output_lookup(output) if recorded is None else recorded
    batch = select_new(candidates, records, limit=limit)

    result = BatchResult(considered=len(batch))
    staged_errors = StagedErrorLog(
        dataset.logs_directory,
        stages=stages or {},
        prefix=dataset.log_prefix,
    )
    takes_scope = _accepts_second_argument(process)

    logger.info("Processing %d items (%d already recorded).", len(batch), len(records))
    batch_start_time = time.monotonic()

    for index, item in enumerate(batch, start=1):
        progress = f"[{index}/{len(batch)}] {item}"
        item_start_time = time.monotonic()

        with staged_errors.item(str(item)) as scope:
            value = process(item, scope) if takes_scope else process(item)

        if scope.failed:
            result.failed += 1
            # Caches with side outputs -- a `checked_at` stamp, the messages explaining a rejection
            # -- need to record something about a failure too, not only the failure value itself.
            if on_error is not None:
                on_error(item, scope)
            if on_failure == RECORD:
                records[item] = failure_value
                result.processed += 1
            else:
                logger.warning("%s: leaving unrecorded for a later run to retry.", progress)
        elif value is NOTHING:
            logger.info("%s: nothing to record.", progress)
        else:
            records[item] = value
            result.processed += 1
            result.succeeded += 1
            detail = f"{describe(value)}; " if describe is not None else ""
            logger.info(
                "%s: %s%.1f s; peak memory %.0f MiB.",
                progress,
                detail,
                time.monotonic() - item_start_time,
                peak_memory_mib(),
            )

        if write and checkpoint_every and index % checkpoint_every == 0:
            dataset.write_output_lookup(records, output)
            logger.info("%s: checkpointed %d records.", progress, len(records))

    result.elapsed_seconds = time.monotonic() - batch_start_time

    if write:
        file_path = dataset.write_output_lookup(records, output)
        logger.info(
            "Wrote %d records to %s (%d new, %d failed) in %.1f min (peak memory %.0f MiB).",
            len(records),
            file_path,
            result.succeeded,
            result.failed,
            result.elapsed_minutes,
            peak_memory_mib(),
        )
    return records, result


def run_full_rebuild(
    dataset: CacheDataset,
    /,
    *,
    build: typing.Callable[[], typing.Iterable],
    limit: int | None = None,
    output: str | None = None,
) -> tuple[list, BatchResult]:
    """Recompute a cache from scratch and write it out, for the cheap pure-filter caches.

    Some caches are a filter over an upstream file rather than an accumulation of expensive work.
    They have nothing to resume, so they recompute everything each run; the `limit` is a bounded
    smoke test rather than a batch size.
    """
    start_time = time.monotonic()
    records = list(build())
    if limit is not None:
        records = list(itertools.islice(records, limit))

    file_path = dataset.write_output_records(records, output)
    result = BatchResult(
        considered=len(records),
        processed=len(records),
        succeeded=len(records),
        elapsed_seconds=time.monotonic() - start_time,
    )
    logger.info(
        "Wrote %d records to %s in %.1f min (peak memory %.0f MiB).",
        len(records),
        file_path,
        result.elapsed_minutes,
        peak_memory_mib(),
    )
    return records, result


def _accepts_second_argument(function: typing.Callable, /) -> bool:
    """Whether `process` wants the per-item error scope as well as the item itself."""
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):  # A builtin or C callable: assume the simple one-argument form.
        return False

    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    if any(parameter.kind is parameter.VAR_POSITIONAL for parameter in signature.parameters.values()):
        return True
    return len(positional) >= 2
