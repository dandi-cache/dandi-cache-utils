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

import dataclasses
import inspect
import itertools
import math
import pathlib
import queue
import threading
import time
import typing

from ._dataset import CacheDataset
from ._logs import StagedErrorLog, logger, peak_memory_mib
from ._logs import memory_limit_mib as _memory_limit_mib

SKIP = "skip"
RECORD = "record"
RETRY = "retry"

#: Returned by an operation to mean "there is nothing to record for this item".
NOTHING = object()

#: How long a batch may run before it stops and writes what it has, by default. An update job is
#: given 330 minutes, and a run killed at that limit publishes nothing, so this leaves an hour and a
#: half for setting up, for the items still in flight to finish, and for validating and publishing.
DEFAULT_TIME_LIMIT_MINUTES = 240


@dataclasses.dataclass
class BatchResult:
    """What one batch did, for the summary line and for tests."""

    considered: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0
    stopped_for_memory: bool = False
    stopped_for_time: bool = False

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
    rotation: int = 0,
) -> list:
    """The items in `universe` not already recorded, sorted, capped at `limit`.

    Sorting matters: iterating a `set` gives a different batch on every run, so a failing item can
    silently rotate in and out of the batch instead of being seen and fixed.

    With `retry_when`, `recorded` is a mapping, and the items of `universe` it records with a value
    `retry_when` accepts follow the unrecorded ones, sorted too. They come last so that an item
    which fails every time it is tried cannot hold back the items that have never been tried.

    The retries start `rotation` places into their sorted order and wrap around. A run that stops
    for its time limit partway through them would otherwise retry the same first ones every time,
    and never reach the rest: `run_incremental_update` passes a rotation that changes from run to
    run, so each retry has its turn.
    """
    universe = list(universe)
    frontier = sorted(item for item in universe if item not in recorded)
    if retry_when is not None:
        retries = sorted(item for item in universe if item in recorded and retry_when(recorded[item]))
        start = rotation % len(retries) if retries else 0
        frontier += retries[start:] + retries[:start]
    return frontier if limit is None else list(itertools.islice(frontier, limit))


def _retry_rotation() -> int:
    """Where this run starts in the sorted retries: the minute it starts, so each run differs."""
    return int(time.time() // 60)


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
    in_memory: bool = True,
    memory_limit_mib: float | None = None,
    time_limit_minutes: float = DEFAULT_TIME_LIMIT_MINUTES,
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

    `in_memory=False` is for an output too large to hold in memory. The recorded items are
    read one at a time, keeping only which are recorded and which `retry_when` selects again, and
    each write appends this batch's results to the output. The cache is then
    never loaded whole, and the mapping returned holds only this batch's results.

    A batch stops early, and writes what it has, once this process's peak memory passes
    `memory_limit_mib` -- by default three quarters of the memory the machine or its container has,
    and `math.inf` turns it off. A process that grows past all of it is killed by the kernel, after a
    long stall, and a killed run publishes nothing: a batch that reached 10 GB on a 16 GB runner lost
    the 2200 results it had checkpointed. `BatchResult.stopped_for_memory` says it happened. The
    limit saves the work, but the growth is still the cache's to fix, usually by running each item
    in a child with `run_isolated`.

    It also stops early, and writes what it has, once it has run for `time_limit_minutes` -- 240 by
    default, and `math.inf` turns it off. A batch of items that each run into their own timeout can
    otherwise outlast the job: a batch of `valid-nwb-file-to-array-sizes` got through 572 of 853
    items in five and a half hours, nearly all of them twenty-minute read timeouts, was killed at the
    job's limit, and published nothing, so not even its recorded failures were kept and the next run
    would have chosen the same items. `BatchResult.stopped_for_time` says it happened.
    The limit also covers an item that never finishes: items run in daemon threads, so one still
    running when the limit passes is abandoned, unrecorded, rather than holding the batch until the
    job is killed. A read of `valid-nwb-file-to-number-of-groups` hung for five hours inside one
    item, where a check between items never got the chance to run.

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

    if in_memory:
        records = dataset.read_output_lookup(output) if recorded is None else recorded
        selection_basis, selection_retry = records, retry_when
    else:
        if recorded is not None:
            raise ValueError("`recorded` holds the whole cache in memory, which `in_memory=False` is there to avoid.")
        records = {}
        # One flag per recorded item, rather than its value: whether `retry_when` selects it again.
        selection_basis = {
            key: retry_when is not None and retry_when(value) for key, value in dataset.iter_output(output)
        }
        selection_retry = None if retry_when is None else bool

    unwritten: list = []
    # A `recorded` mapping is the caller's, and the caller may have changed it before handing it over:
    # added entries that cost no processing, corrected some, dropped what its source no longer lists.
    # Appending only this batch's results would silently lose all of that, so the first write records
    # the whole mapping, appending what differs and rewriting the output only if an entry was dropped.
    reconcile = [recorded is not None]

    def write_records() -> pathlib.Path:
        if reconcile[0]:
            reconcile[0] = False
            unwritten.clear()
            return dataset.write_output_lookup(records, output)
        # Only what this batch added since the last write is appended, whether or not the whole cache is
        # held in memory: an output only ever grows, so nothing already in it is rewritten.
        file_path = dataset.merge_output_lookup({key: records[key] for key in unwritten}, output)
        unwritten.clear()
        return file_path

    universe = None if candidates is None else list(candidates)
    batch = (
        list(batch)
        if universe is None
        else select_new(universe, selection_basis, limit=limit, retry_when=selection_retry, rotation=_retry_rotation())
    )

    limit_mib = memory_limit_mib if memory_limit_mib is not None else _default_memory_limit_mib()
    result = BatchResult(considered=len(batch))
    staged_errors = StagedErrorLog(
        dataset.logs_directory,
        stages=stages or {},
        prefix=dataset.log_prefix,
    )
    takes_scope = _accepts_second_argument(process)

    logger.info("Processing %d items (%d already recorded).", len(batch), len(selection_basis))
    batch_start_time = time.monotonic()

    def attempt(item: typing.Any) -> tuple[typing.Any, typing.Any, typing.Any, float]:
        item_start_time = time.monotonic()
        value = None
        with staged_errors.item(str(item)) as scope:
            value = process(item, scope) if takes_scope else process(item)
        return item, scope, value, time.monotonic() - item_start_time

    deadline = batch_start_time + 60 * time_limit_minutes
    outcomes = _in_daemon_threads(attempt, batch, workers=workers, deadline=deadline)

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
                    unwritten.append(item)
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
                unwritten.append(item)
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
                write_records()
                if on_write is not None:
                    on_write()
                logger.info("%s: checkpointed %d new records.", progress, len(records))

            if time.monotonic() - batch_start_time >= 60 * time_limit_minutes:
                result.stopped_for_time = True
                logger.warning(
                    "%s: the batch has run for its %.0f-minute limit, so it stops after %d of %d items and "
                    "writes what it has, rather than being killed with the job and losing it.",
                    progress,
                    time_limit_minutes,
                    index,
                    len(batch),
                )
                break

            if peak_memory_mib() > limit_mib:
                result.stopped_for_memory = True
                logger.warning(
                    "%s: peak memory %.0f MiB passed the %.0f MiB limit, so the batch stops after %d of %d items "
                    "and writes what it has, rather than being killed and losing it.",
                    progress,
                    peak_memory_mib(),
                    limit_mib,
                    index,
                    len(batch),
                )
                break
    except _Overdue as overdue:
        result.stopped_for_time = True
        logger.warning(
            "The batch has run for its %.0f-minute limit while %s still running, so it abandons %s and "
            "writes what it has, rather than being killed with the job and losing it: %s.",
            time_limit_minutes,
            "one item was" if len(overdue.items) == 1 else f"{len(overdue.items)} items were",
            "it" if len(overdue.items) == 1 else "them",
            ", ".join(str(item) for item in overdue.items),
        )

    result.elapsed_seconds = time.monotonic() - batch_start_time

    if write:
        file_path = write_records()
        if on_write is not None:
            on_write()
        if dataset.compact_output_if_superseded(output):
            logger.info("Compacted %s: most of the entries it held were superseded by later ones.", file_path)
        logger.info(
            "Wrote %d records to %s (%d new, %d failed) in %.1f min (peak memory %.0f MiB).",
            len(records) if in_memory else len(selection_basis) + len(set(records) - set(selection_basis)),
            file_path,
            result.succeeded,
            result.failed,
            result.elapsed_minutes,
            peak_memory_mib(),
        )
    if universe is not None:
        # One line a reader such as the cache dashboard can parse: how much of the cache's candidate set is
        # recorded now. A recorded failure counts, as the item has been tried.
        candidate_set = set(universe)
        log_progress(sum(1 for item in candidate_set if item in selection_basis or item in records), len(candidate_set))
    return records, result


#: How long an item may still take once the time limit has passed before it is abandoned, so one that
#: is about to finish anyway is recorded rather than thrown away.
_OVERDUE_GRACE_SECONDS = 1.0


class _Overdue(Exception):
    """The batch's time limit passed while these items were still running."""

    def __init__(self, items: list) -> None:
        super().__init__(items)
        self.items = items


def _in_daemon_threads(
    attempt: typing.Callable[[typing.Any], tuple],
    batch: typing.Sequence,
    /,
    *,
    workers: int,
    deadline: float,
) -> typing.Iterator[tuple]:
    """Yield `attempt(item)` for each item as it finishes, at most `workers` at a time.

    Daemon threads rather than a thread pool, because a pool's threads are joined when the interpreter
    exits, so one stuck in a read that never returns would keep the process alive until the job is
    killed. Raises `_Overdue` once `deadline` (a `time.monotonic()` value) passes while waiting, naming
    the items still running.

    The next item starts only once the caller has finished with the last result, so with one worker the
    items run strictly one after another, and recording a result never overlaps with processing the next.
    """
    results: queue.Queue = queue.Queue()
    running: dict = {}
    remaining = enumerate(batch)

    def run(key: int, item: typing.Any) -> None:
        try:
            results.put((key, attempt(item), None))
        except BaseException as exception:  # Re-raised on the calling thread, where it belongs.
            results.put((key, None, exception))

    def start_next() -> None:
        for key, item in remaining:
            running[key] = item
            threading.Thread(target=run, args=(key, item), daemon=True).start()
            return

    for _ in range(workers):
        start_next()

    while running:
        timeout = None if math.isinf(deadline) else max(_OVERDUE_GRACE_SECONDS, deadline - time.monotonic())
        try:
            key, outcome, exception = results.get(timeout=timeout)
        except queue.Empty:
            raise _Overdue(list(running.values())) from None
        del running[key]
        if exception is not None:
            raise exception
        yield outcome
        start_next()


def log_progress(done: int, total: int, /) -> None:
    """Log how much of a cache's candidates is done, as the one line the cache dashboard reads.

    `run_incremental_update` given `candidates` logs it itself. A cache that selects its own batch,
    or reads its whole source every run, calls this with what it has tried out of what there is: an
    item that was tried and could not be read counts, as it has been tried.
    """
    logger.info(
        "Progress: %s of %s candidates recorded (%.1f%%).",
        f"{done:,}",
        f"{total:,}",
        100 * done / total if total else 100.0,
    )


def _default_memory_limit_mib() -> float:
    # Its own function, because inside `run_incremental_update` the parameter shadows the helper's name.
    return _memory_limit_mib() or math.inf


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
