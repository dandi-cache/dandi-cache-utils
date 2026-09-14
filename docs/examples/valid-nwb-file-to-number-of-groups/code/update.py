"""Count the internal groups of every valid NWB file (HDF5 or Zarr).

The real `code/update.py` of dandi-cache/valid-nwb-file-to-number-of-groups, rewritten on
`dandi_cache_utils`: 245 lines become these. Everything removed was shared with its siblings --
the logging setup, the peak-memory helper, two copies of the same JSONL loader, the argument
parser, the testing-mode file switching, the incremental frontier, the batch loop with its
progress and summary lines, and the S3 layout probe with the HDF5 and Zarr walks.
"""

import dandi_cache_utils as dandi_cache


def count_groups(content_id: str, item) -> int:
    """Count the groups in one asset, resolved straight from its content ID.

    The archive is content-addressed, so no DANDI API lookup is needed: the blob key is probed and
    the asset is read as Zarr when no such blob exists.
    """
    item.stage = "reading the NWB file"
    return dandi_cache.nwb.walk_structure(content_id).number_of_groups


def main() -> None:
    dataset, arguments = dandi_cache.open_dataset()

    # Only the assets the upstream cache marked valid are counted.
    validity = dataset.read_input()
    valid_content_ids = [content_id for content_id, is_valid in validity.items() if is_valid is True]

    dandi_cache.run_incremental_update(
        dataset,
        candidates=valid_content_ids,
        process=count_groups,
        limit=dandi_cache.effective_limit(testing=dataset.testing, limit=arguments.limit),
        # These files were already opened successfully upstream, so a failure here is almost always
        # transient. Leave the item for a later run rather than recording a wrong count.
        on_failure=dandi_cache.SKIP,
        stages={"reading the NWB file": "file_read_errors.txt"},
        describe=lambda number_of_groups: f"{number_of_groups} groups",
        checkpoint_every=50,
    )


if __name__ == "__main__":
    main()
