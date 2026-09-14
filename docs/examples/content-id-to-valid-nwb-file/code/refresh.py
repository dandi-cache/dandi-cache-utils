"""Re-assess the content IDs most overdue for a check against a newer NWB Inspector.

The real `code/refresh.py` of dandi-cache/content-id-to-valid-nwb-file, rewritten on
`dandi_cache_utils`. It is the same work as `update.py` on a different selection, which is all that
is left here: oldest `checked_at` first, sized so that a daily run cycles the whole cache monthly.

The pipeline runs it with `OPERATION=refresh`, which reads `[operations.refresh]` from
`cache.toml`. That replaces the two bespoke environment variables the shell script used to thread
through for this one cache.
"""

from _common import CHECKED_AT, STAGES, VALIDITY, Assessor

import dandi_cache_utils as dandi_cache

# A daily run re-checks this share of the cache, so the whole of it is cycled about once a month.
FRACTION_PER_RUN = 1 / 30


def main() -> None:
    dataset, arguments = dandi_cache.open_dataset(operation="refresh")

    locations = dataset.read_input()
    assessor = Assessor(dataset, locations)
    already_assessed = dataset.read_output_lookup(VALIDITY)

    # Only content IDs that are both already recorded and still present upstream are re-assessed.
    overdue = dandi_cache.select_stale(
        set(already_assessed) & set(locations),
        assessor.checked_at,
        limit=dandi_cache.effective_limit(testing=dataset.testing, limit=arguments.limit),
        fraction_per_run=FRACTION_PER_RUN,
    )

    # An empty `recorded` and `write=False` turn the shared loop into "process exactly this
    # selection, tell me the results": a refresh reprocesses what an update deliberately skips, so
    # it cannot use the recorded mapping as its frontier.
    refreshed, _result = dandi_cache.run_incremental_update(
        dataset,
        candidates=overdue,
        recorded={},
        process=assessor.assess,
        output=VALIDITY,
        on_failure=dandi_cache.RECORD,
        failure_value=False,
        on_error=assessor.record_failure,
        stages=STAGES,
        write=False,
    )

    # The re-assessed values replace the recorded ones; everything untouched keeps its value.
    already_assessed.update(refreshed)
    dataset.write_output_lookup(already_assessed, VALIDITY)
    assessor.write_side_outputs()

    dandi_cache.logger.info("Re-assessed %d of %d recorded content IDs.", len(overdue), len(already_assessed))
    dandi_cache.logger.info("Assessment dates on file: %d.", len(dataset.read_output_lookup(CHECKED_AT)))


if __name__ == "__main__":
    main()
