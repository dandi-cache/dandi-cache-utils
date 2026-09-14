# Migrating an existing cache

One cache at a time, each in its own pull request. Nothing here needs a flag day: a cache that has
not been migrated keeps working exactly as it does now, because this library adds files rather than
changing any.

Do the migration in the order below and stop at step 5 to verify before deleting anything.


## 1. Write `cache.toml`

Copy [`cache.toml`](https://github.com/dandi-cache/cache-template/blob/main/cache.toml) from
`cache-template` to the repository root and fill it in from what the repository already says:

- `cache.name` — the repository name.
- `[[inputs]]` — one entry per `INPUT_SUBDATASET_URL` (or per bespoke `*_SUBDATASET_*` pair) in
  `code/update_pipeline.sh`. Only `name` is needed unless the upstream publishes on a branch other
  than `derivatives` or under a file name that is not the underscored form of its own name.
- `format` on each input — `lookup` if the update code merges the lines into one mapping,
  `records` if it keeps them as a list, `ids` if the lines are bare scalars.
- `cache.outputs` — every file the cache writes into `derivatives/`. Omit it for a single output
  named after the cache.
- `[operations.update] limit` — the batch size the workflow or the shell script defaults to today.
  Check both: in at least one cache they disagree, and the scheduled value is the real one.
- `[description]` — the `Authors` and any non-default fields from `dataset_description.json`.

Check it with:

```bash
docker run --rm -v "$PWD":/w:ro ghcr.io/dandi-cache/dandi-cache-utils:latest \
  dandi-cache config show /w/cache.toml
```


## 2. Rewrite `code/update.py`

Delete the boilerplate and keep the operation. In practice that means removing
`_configure_logging`, `_peak_memory_mib`, every `_load_*` and `_write_*` helper, the argument
parser, the testing-mode file switching, the frontier computation and the batch loop — then calling
`run_incremental_update` with what is left.

Pick the failure policy deliberately, since it is the one thing the shared loop will not guess:

- `dandi_cache.SKIP` if the work is known to be possible and a failure is almost always transient,
  so a later run should retry it.
- `dandi_cache.RECORD` with a `failure_value` if the failure is itself the answer, so the item
  should never be retried.

Whatever the repository does today is the answer; make it explicit.

The three shapes are worked through in [the examples](../examples/index.md):

| Example | Shape |
|---|---|
| [`valid-nwb-file-to-number-of-groups`](../examples/valid-nwb-file-to-number-of-groups.md) | Incremental, heavy per item, skip on failure, one output |
| [`content-id-to-nwb-file`](../examples/content-id-to-nwb-file.md) | A cheap filter with nothing to resume, so a full rebuild each run |
| [`content-id-to-valid-nwb-file`](../examples/content-id-to-valid-nwb-file.md) | Incremental, three parallel outputs, record on failure, plus a second `refresh` entry point |


## 3. Replace the container

```dockerfile
FROM ghcr.io/dandi-cache/dandi-cache-utils:latest
LABEL org.opencontainers.image.source="https://github.com/dandi-cache/<cache-name>"
LABEL org.opencontainers.image.description="The pinned runtime environment for the DANDI Cache <cache-name> update pipeline."
RUN pip install <dependencies used by this cache>
```

Use `:nwb` instead of `:latest` if the cache streams remote NWB files, and drop the `pip install`
line entirely if that tag already covers everything the cache needs.

Do **not** carry `datalad` or `datalad-container` over. They never ran inside the image; the
pipeline installs them on the runner from its own pinned requirements.


## 4. Replace the workflows

Both become a few lines that delegate to the shared workflows — see
[`cache-template`'s workflows](https://github.com/dandi-cache/cache-template/tree/main/.github/workflows).
Keep the schedule, the concurrency group and the dispatch inputs; everything else goes.

A cache with a second entry point passes it through:

```yaml
jobs:
  Refresh:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: read
    steps:
      - uses: dandi-cache/dandi-cache-actions@v0
        with:
          token: ${{ secrets._GITHUB_API_KEY }}
          operation: refresh
          limit: ${{ inputs.limit || '' }}
```

Keep a cross-workflow concurrency group where one already exists: two workflows that both push to
`derivatives` must not run at once.


## 5. Verify before deleting

Merge the image change first so the new base image is published, then dispatch `Update` manually
with `testing: true`. A testing run writes `testing_`-prefixed files and never touches the real
cache, so it is safe on a live repository. Confirm that:

- the run completes and the `derivatives` branch has a new `[DATALAD RUNCMD]` commit;
- that commit's log under `logs/` shows one line per item;
- the real cache file is unchanged, and no `testing_` file reached `dist`.

Then run a complete update and compare the diff on `derivatives` against what a normal run would
have produced.


## 6. Delete what is now shared

- `code/update_pipeline.sh`
- `code/compress.py`
- `code/_pipeline_common.py`, if the repository has one
- `envs/`, including `envs/pyproject.toml` and `envs/.python-version`
- `dataset_description.json` — rendered from `cache.toml` onto both published branches

Keep `.pre-commit-config.yaml`, the root `pyproject.toml`, `.github/dependabot.yml` and the issue
templates: they are per-repository by nature, and Dependabot has almost nothing left to update once
the workflows are three lines.


## Notes on two behaviour changes

**The container mount moved.** The code checkout is now mounted at `/workspace` rather than
`code/` at `/code`, so the entry point runs as `python /workspace/code/update.py` and finds
`cache.toml` beside it. Existing `[DATALAD RUNCMD]` records keep their old command string and stay
valid; new ones use the new one.

**`dist` publishes a declared list.** Previously some caches published a named file and others
globbed `derivatives/*.jsonl.gz`. Now `cache.toml` says which files are published. If a declared
output is missing after a run, the pipeline warns and publishes the rest; if none were produced, it
leaves `dist` alone rather than replacing it with an empty artifact.
