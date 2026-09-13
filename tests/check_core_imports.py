"""Assert that importing the core pulls in nothing outside the standard library.

`bin/update_pipeline.sh` parses `cache.toml` with the CI runner's bare `python3`, before any
environment has been built. If anything in the core grows an import of boto3, h5py or pynwb, the
orchestration stops working before the run even starts -- and it would fail in CI rather than in
anyone's editor, so it is checked here.

Run as a script, not under pytest: it has to observe a clean interpreter's `sys.modules`.
"""

import sys

BLOCKED = {"boto3", "botocore", "dandi", "h5py", "hdmf_zarr", "nwbinspector", "pynwb", "remfile", "s3fs", "zarr"}


def main() -> int:
    import dandi_cache_utils  # noqa: F401
    import dandi_cache_utils.cli  # noqa: F401
    import dandi_cache_utils.config  # noqa: F401
    import dandi_cache_utils.dataset  # noqa: F401
    import dandi_cache_utils.jsonl  # noqa: F401
    import dandi_cache_utils.logs  # noqa: F401
    import dandi_cache_utils.runner  # noqa: F401

    loaded = sorted(BLOCKED & set(sys.modules))
    if loaded:
        print(f"FAIL: the core imported third-party modules: {loaded}", file=sys.stderr)
        return 1
    print("OK: the core is standard-library only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
