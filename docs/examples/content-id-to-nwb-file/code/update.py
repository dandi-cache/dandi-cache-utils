"""Keep the upstream entries whose asset path is an NWB file.

The real `code/update.py` of dandi-cache/content-id-to-nwb-file, rewritten on
`dandi_cache_utils`. This is the cheap shape: no network, no per-item cost, and nothing to resume,
so it recomputes the whole subset each run. The batch cap is a bounded smoke test rather than a
backlog policy, which is exactly what `run_full_rebuild` expresses.

The `.nwb` suffix test lived here as an inline expression, in a helper in one sibling, and in a
third spelling in another. There is one of it now, and it keeps `.nwb.zarr` in all three places.
"""

import dandi_cache_utils as dandi_cache


def main() -> None:
    dataset, arguments = dandi_cache.open_dataset()
    upstream = dataset.read_input()

    def nwb_records():
        """The upstream records, as-is, whose single asset path names an NWB file."""
        for record in upstream:
            ((_content_id, location),) = record.items()
            _dandiset_id, path = dandi_cache.api.split_location(location)
            if dandi_cache.nwb.is_nwb_path(path):
                yield record

    dandi_cache.run_full_rebuild(
        dataset,
        build=nwb_records,
        limit=dandi_cache.effective_limit(testing=dataset.testing, limit=arguments.limit),
    )


if __name__ == "__main__":
    main()
