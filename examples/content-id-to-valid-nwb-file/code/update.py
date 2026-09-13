"""Assess every not-yet-checked NWB file against the NWB Inspector.

The real `code/update.py` of dandi-cache/content-id-to-valid-nwb-file, rewritten on
`dandi_cache_utils`. This is the multi-output shape: one cache, three parallel files keyed by
content ID, all declared in `cache.toml` so the pipeline knows exactly what to publish.
"""

from _common import CHECKED_AT, STAGES, VALIDITY, Assessor

import dandi_cache_utils as dandi_cache


def main() -> None:
    dataset, arguments = dandi_cache.open_dataset()

    locations = dataset.read_input()
    assessor = Assessor(dataset, locations)

    # Already-assessed content IDs are exactly the keys already recorded, success or failure alike,
    # so a re-run only picks up new ones. Re-assessing what is already recorded is refresh.py's job.
    dandi_cache.run_incremental_update(
        dataset,
        candidates=list(locations),
        process=assessor.assess,
        limit=dandi_cache.effective_limit(testing=dataset.testing, limit=arguments.limit),
        output=VALIDITY,
        # A file that cannot be opened or inspected is not valid: that failure is the answer, so it
        # is recorded rather than retried forever.
        on_failure=dandi_cache.RECORD,
        failure_value=False,
        on_error=assessor.record_failure,
        stages=STAGES,
        checkpoint_every=50,
    )
    assessor.write_side_outputs()

    dandi_cache.logger.info("Recorded %d assessment dates.", len(dataset.read_output_lookup(CHECKED_AT)))


if __name__ == "__main__":
    main()
