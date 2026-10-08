"""The update loop that every cache runs, minus the one operation that makes it different.

Each cache does the same six things: work out which items are not yet recorded, cap the batch,
process each item, log a line about it, decide what a failure means, and write the result out.
Only the per-item operation differs. `run_incremental_update` is that loop; the cache supplies
the operation.

One invariant holds across both models, and it is the reason the limit lives where it does:
**a limit bounds the work a run does, never the records it publishes.** Every cache declares how
much one scheduled run gets through, so a backlog is cleared over several crons rather than in
one; what each of those runs writes is still the complete cache. Applying the cap to the output
instead would publish a truncated file and delete the rest of the cache from every consumer, which
is why there is nowhere in this module to do it.

Three failure policies are supported explicitly, because choosing the wrong one is a real bug:

- `skip` leaves a failed item unrecorded so a later run retries it. Correct when the work is
  known to be possible and a failure is almost always transient, e.g. a network read of a file
  that upstream already opened successfully.
- `record` writes `failure_value` for the item so it is never retried. Correct when the failure
  *is* the answer, e.g. a file that cannot be validated does not qualify.
- `retry` writes `failure_value` too, so the failure and its reason are published, but selects the
  item again on a later run once every item never tried has had its turn. Correct when a consumer
  needs to see what failed and why, and the failure may still clear, e.g. a walk that timed out.
  `retry_when` says which recorded values are failures worth another attempt, since a cache can
  publish a permanent failure beside a transient one.
"""

import concurrent.futures
import dataclasses
import inspect
import itertools
import math
import time
import typing

from ._dataset import CacheDataset
from ._logs import StagedErrorLog, logger, peak_memory_mib

SKIP = "skip"
RECORD = "record"
RETRY = "retry"

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
    retry_when: typing.Callable[[typing.Any], bool] | None = None,
) -> list:
    """The items in `universe` not already recorded, sorted, capped at `limit`.

    Sorting matters: iterating a `set` gives a different batch on every run, so a failing item can
    silently rotate in and out of the batch instead of being seen and fixed.

    With `retry_when`, `recorded` is a mapping, and the items of `universe` it records with a value
    `retry_when` accepts follow the unrecorded ones, sorted too. They come last so that an item
    which fails every time it is tried cannot hold back the items that have never been tried.
    """
    universe = list(universe)
    frontier = sorted(item for item in universe if item not in recorded)
    if retry_when is not None:
        frontier += sorted(item for item in universe if item in recorded and retry_when(recorded[item]))
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


def run_incremental_update(
    dataset: CacheDataset,
    /,
    *,
    candidates: typing.Iterable | None = None,
    batch: typing.Iterable | None = None,
    process: typing.Callable[..., typing.Any],
    limit: int | None = None,
    output: str | None = None,
    recorded: typing.MutableMapping | None = None,
    on_failure: str = SKIP,
    failure_value: typing.Any = False,
    retry_when: typing.Callable[[typing.Any], bool] | None = None,
    on_error: typing.Callable[[typing.Any, typing.Any], None] | None = None,
    on_write: typing.Callable[[], None] | None = None,
    describe: typing.Callable[[typing.Any], str] | None = None,
    stages: typing.Mapping[str, str] | None = None,
    checkpoint_every: int | None = None,
    write: bool = True,
    workers: int = 1,
) -> tuple[dict, BatchResult]:
    """Process a batch of items and write the updated cache.

    Give `candidates` for the ordinary case, where the batch is whatever is not recorded yet,
    capped at `limit`. Give `batch` instead when the cache selects its own items -- a `refresh`
    re-assessing what it already recorded, built with `select_stale` -- since those are recorded by
    definition and `limit` has already been applied in selecting them.

    `process` is called with the item, and with the per-item error scope as a second argument when
    it accepts one. Setting `item.stage` on that scope is what routes a failure to the right error
    log, and anything put in `item.context` is reported with it, so a failure names the asset it was
    working on rather than only the item's key. Returning `NOTHING` records nothing for the item
    without counting as a failure.
    `failure_value` is written for a failure under `RECORD` and `RETRY`. When it is callable, it is
    called as `failure_value(item, scope)` and its result written instead, so the record can say
    what went wrong: `scope.exception` is what was raised. `retry_when(value)` says which recorded
    values `RETRY` selects again, and is required with it.
    `on_error(item, scope)` is called for each failure, for caches that keep side outputs about
    why an item failed, and `on_write()` after each write of the cache itself, for caches that keep
    those side outputs in files of their own and need all of them to land together.

    `workers` processes that many items at once, in threads, for an operation that spends its time
    waiting on the network or on a child process, such as one run through `run_isolated`. Only
    `process` runs in those threads, so it must be safe to call concurrently; recording each result,
    logging it, `on_error`, `on_write` and checkpoints all happen on the calling thread, in the order
    items finish.

    Returns the full `{item: value}` mapping and a `BatchResult` describing the batch.
    """
    if workers < 1:
        raise ValueError(f"workers must be at least 1, got {workers}.")
    if on_failure not in (SKIP, RECORD, RETRY):
        raise ValueError(f"on_failure must be {SKIP!r}, {RECORD!r} or {RETRY!r}, got {on_failure!r}.")
    if (on_failure == RETRY) != (retry_when is not None):
        raise ValueError("`retry_when` is required with `on_failure=RETRY`, and means nothing without it.")
    if (candidates is None) == (batch is None):
        raise ValueError("Pass either `candidates`, to select the unrecorded ones, or `batch`, already selected.")

    records = dataset.read_output_lookup(output) if recorded is None else recorded
    batch = list(batch) if candidates is None else select_new(candidates, records, limit=limit, retry_when=retry_when)

    result = BatchResult(considered=len(batch))
    staged_errors = StagedErrorLog(
        dataset.logs_directory,
        stages=stages or {},
        prefix=dataset.log_prefix,
    )
    takes_scope = _accepts_second_argument(process)

    logger.info("Processing %d items (%d already recorded).", len(batch), len(records))
    batch_start_time = time.monotonic()

    def attempt(item: typing.Any) -> tuple[typing.Any, typing.Any, typing.Any, float]:
        item_start_time = time.monotonic()
        value = None
        with staged_errors.item(str(item)) as scope:
            value = process(item, scope) if takes_scope else process(item)
        return item, scope, value, time.monotonic() - item_start_time

    if workers == 1:
        outcomes: typing.Iterable = map(attempt, batch)
        executor = None
    else:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        futures = [executor.submit(attempt, item) for item in batch]
        outcomes = (future.result() for future in concurrent.futures.as_completed(futures))

    try:
        for index, (item, scope, value, item_seconds) in enumerate(outcomes, start=1):
            progress = f"[{index}/{len(batch)}] {item}"

            if scope.failed:
                result.failed += 1
                # Caches with side outputs -- a `checked_at` stamp, the messages explaining a rejection
                # -- need to record something about a failure too, not only the failure value itself.
                if on_error is not None:
                    on_error(item, scope)
                if on_failure in (RECORD, RETRY):
                    records[item] = failure_value(item, scope) if callable(failure_value) else failure_value
                    result.processed += 1
                    if on_failure == RETRY:
                        retried = "; a later run retries it" if retry_when(records[item]) else ", not to be retried"
                        logger.warning("%s: recorded the failure%s.", progress, retried)
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
                    item_seconds,
                    peak_memory_mib(),
                )

            if write and checkpoint_every and index % checkpoint_every == 0:
                dataset.write_output_lookup(records, output)
                if on_write is not None:
                    on_write()
                logger.info("%s: checkpointed %d records.", progress, len(records))
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    result.elapsed_seconds = time.monotonic() - batch_start_time

    if write:
        file_path = dataset.write_output_lookup(records, output)
        if on_write is not None:
            on_write()
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
    output: str | None = None,
) -> tuple[list, BatchResult]:
    """Write a cache that was recomputed in full, for caches with nothing to resume.

    Some caches are a derivation of their inputs rather than an accumulation of expensive work:
    a filter, a join, a reshaping. They have no frontier, so they recompute everything each run
    and this writes the result.

    There is deliberately no `limit` here. A cache meters itself by bounding the *work* it does --
    which items it fetches, which Dandisets it reads -- inside `build`, and then publishes
    everything it knows. Capping the records on the way out would instead delete the rest of the
    cache from the published file, which is never what a limit is asked for. `dataset.limit()`
    resolves the cap; where it is applied is what makes it safe, so it belongs upstream of here.
    """
    start_time = time.monotonic()
    records = list(build())

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
