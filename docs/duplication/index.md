# What was duplicated

The caches were generated from a common template and then diverged. The same fix had to be written
repeatedly, in different ways, and reached a different subset of repositories each time:

| Fix | template | qualifying-aind | content-id-to-nwb-file | content-id-to-usage-dandiset-path | content-id-to-valid-nwb-file | valid-nwb-file-to-number-of-groups |
|---|---|---|---|---|---|---|
| Retry a transient push rejection | yes | yes, rewritten | no | no | no | no |
| Retry a transient `docker pull` | yes | no | no | no | no | yes, rewritten |
| Pre-run dataset cleanliness check | yes | **missing** | yes, rewritten | yes, rewritten | yes, rewritten | yes |
| Save the advanced input pointer with `-d .` | yes | no | yes | yes | yes | yes |
| Keep each run's log on `derivatives` | yes | yes | no | no | yes | yes |

Four repositories fixed "the input subdataset pointer is silently not saved" in four separate pull
requests. Three fixed the cleanliness check that misfires on a clean dataset, in two different
ways. The push retry exists in two incompatible implementations and is absent from three caches.
None of that is per-cache behaviour; it is one pipeline maintained five times.

The rest of this page is the full analysis behind that.

An analysis of the six repositories available when this library was written — `cache-template`,
`qualifying-aind-content-ids`, `content-id-to-nwb-file`, `content-id-to-usage-dandiset-path`,
`content-id-to-valid-nwb-file` and `valid-nwb-file-to-number-of-groups` — and of what each part of
them actually is: shared infrastructure, a per-cache name, or genuinely different behaviour.

The organization holds seventeen caches, so every number below understates the real total.


## The shape they all share

Each cache is the same pipeline:

1. `main` holds the code; `derivatives` is a persistent DataLad dataset on its own branch; `dist`
   is a force-recreated publication artifact.
2. CI clones `derivatives`, advances its input subdatasets, pins the runtime image by digest,
   registers it as a DataLad container, and runs the update under `datalad containers-run` so the
   command, the input commits, the output diff and the image digest are all recorded.
3. The results are pushed to `derivatives`, gzipped, and force-published to `dist`.

Only step 2's innermost command differs between caches.


## The orchestration: one script, six divergent copies

`code/update_pipeline.sh` is 175–225 lines in each repository. Most of it is byte-identical
everywhere: `set -euo pipefail`, the three required environment variables, the bot identity, the
scratch paths, the clone-or-bootstrap branch, the digest pin and `containers-add --call-fmt`, the
`compress.py` call, and the whole `dist` publication tail.

What diverged is the hard-won part — every fix that was made after the copies were taken:

| Concern | Where it stands |
|---|---|
| Transient push rejection | A generic `retry_with_backoff` in the template; a different implementation in `qualifying-aind-content-ids` that also reconciles the remote SHA; absent in three caches |
| Transient `docker pull` denial | `retry_with_backoff` in the template; a separate inline three-attempt loop in `valid-nwb-file-to-number-of-groups`; absent in three |
| Pre-run cleanliness check | `datalad -f json status` in two; `git status --porcelain` in two; a literal string comparison in one; absent in `qualifying-aind-content-ids` |
| Saving the advanced input pointer | `datalad save -d .` in five, after four separate pull requests fixing the same silent no-op; `-d "${DS}"` with `\|\| true` in one |
| Keeping the run log on `derivatives` | `--output logs` in four; absent in two |
| Never fetching the recorded input commit | Only in `qualifying-aind-content-ids`, which hit the failure when an upstream cache rewrote its history |
| Publishing to `dist` | A named file guarded by a `-f` test in three; an unguarded named file in one; an unguarded `*.jsonl.gz` glob in two |

The last row is a correctness difference, not a style one: the two caches that glob publish
whatever is in `derivatives`, including a `testing.jsonl.gz` left behind by a smoke run.

Two of the six also handle the number of inputs differently. `qualifying-aind-content-ids` has
three input subdatasets and spells each out with its own pair of variables and its own registration
block; the rest have one, driven by a single set of `INPUT_SUBDATASET_*` variables. Nothing about
that is per-cache logic — it is a list that was written as straight-line code.

The reconciling push retry, the never-fetch-the-recorded-commit fix and the idempotent input
registration are all improvements that exist in exactly one repository each. Folding them into one
script is how every cache gets all three.


## The CI: identical but for a name

`.github/workflows/update.yml` is ~130 lines and differs by the cron expression, the image
reference, the notification subject, and the dispatch input. Everything else — the permissions, the
`GITHUB_TOKEN` environment, the four steps (checkout, uv, git-annex, registry login), and the
`NotifyOnFailure` job with its SMTP settings and recipient — is the same text in all six.

The exceptions are each a defect rather than a difference:

- Two caches replace the `Gate` job with an in-job `gh run cancel` plus `sleep 60`, which needs
  `actions: write` on the update job and cancels a run rather than skipping it.
- Only `qualifying-aind-content-ids` tolerates a failing `apt-get update`, so the other five hard
  fail when a preinstalled third-party apt repository has a bad day.
- `qualifying-aind-content-ids` omits `permissions: {}` on both notification jobs.
- No cache sets `timeout-minutes`, so a hung network read burns the full six-hour job limit.
- `content-id-to-valid-nwb-file`'s dispatch default is 10000 while its scheduled fallback is 500,
  so a manual run and a scheduled run do measurably different amounts of work.
- Three cron comments claim "daily at 06:00 UTC" for step expressions that fire every two, four or
  six hours.

`containers/Dockerfile` is byte-identical in all six but for two `LABEL` values. `envs/pyproject.toml`
carries `datalad` and `datalad-container` in every cache even though datalad only ever runs on the
runner, never inside the image.


## The Python: the boilerplate outweighs the operation

Across the six repositories, `code/*.py` totals about 1,570 lines. The genuinely per-cache logic is
a small fraction of it.

- **The JSONL mapping loader** appears four times under three names — `_load_dict`,
  `load_records`, `_load_content_id_to_validity` and `_load_previous_cache` — and the last two are
  the same function body twice in one file with different docstrings.
- **The writer** appears three times, the list-of-records writer three more.
- **`compress.py`** is duplicated six times. Two copies still use `gzip.open`, which stamps an
  mtime into the header, so those two caches republish a byte-different `dist` artifact on every
  run even when the cache has not changed.
- **Structured logging** and `_peak_memory_mib()` exist in two of six. The other four use `print`,
  or produce no per-item output at all, so a run killed mid-batch leaves no record of where it got
  to — which matters most for exactly the caches that are heavy enough to be killed.
- **Error logging** exists in three incompatible forms. Only `qualifying-aind-content-ids` caps the
  file size, and it has to: these logs accumulate forever on a persistent branch and GitHub rejects
  any blob over 100 MB.
- **The incremental frontier** — `upstream.keys() - recorded.keys()`, then `islice` — appears three
  times. Two sort first; `qualifying-aind-content-ids` slices an unordered `set`, so its batch
  selection is not reproducible between runs and a failing item can rotate in and out of the batch
  instead of being seen.
- **The DANDI resolution triple** (`get_dandiset` → `get_asset_by_path` → `get_content_url`) is
  written out in three caches, twice with the same comment.
- **The NWB suffix predicate** exists in three copies and two spellings, and **the HDF5-versus-Zarr
  dispatch** in two.
- **No cache checkpoints.** Every one accumulates in memory and writes once at the end, so a run
  killed mid-batch loses all of it. That is precisely why each has a small batch cap.


## What is genuinely per-cache

Worth stating plainly, because it is what the library must not try to absorb:

- The operation itself: the SpikeInterface qualification checks, the `.nwb` suffix filter, the
  earliest-Dandiset and earliest-asset disambiguation heuristics, the NWB Inspector pass, the
  structural counts.
- The record schema, and how many output files a cache publishes (one, three, or four).
- Which upstream caches are inputs, and the rules that combine them — such as "upstream already
  says this is invalid, so record `false` without doing the work".
- The batch-size policy, and the failure policy: recording `false` for a failed item versus leaving
  it for a later run. Both are correct, for different caches, and choosing wrongly is a real bug —
  so the shared runner asks for the choice explicitly rather than picking one.
- `content-id-to-valid-nwb-file`'s staleness ordering, which no other cache needs.


## What this repository does about it

| Duplicated thing | Where it lives now |
|---|---|
| The 200-line orchestration, six times | `dandi_cache_utils.pipeline`, vendored in the image |
| The update and build workflows, six times | Two actions in [`dandi-cache-action`](https://github.com/dandi-cache/dandi-cache-action) |
| The Dockerfile, six times | `containers/Dockerfile`, published as the base image |
| Input URLs, paths, branches, outputs, entry points, batch sizes | `cache.toml`, read by both the shell and the Python |
| `dataset_description.json` | Rendered from `cache.toml` |
| `compress.py`, six times | `dandi_cache_utils.jsonl`, exposed as `dandi-cache compress` |
| The loaders and writers | `dandi_cache_utils.jsonl` |
| Logging, peak memory, error logs | `dandi_cache_utils.logs` |
| The frontier, the batch loop, the failure policies | `dandi_cache_utils.runner` |
| `--testing` / `--limit` | `dandi_cache_utils.cli` |
| The archive access and NWB reading | `dandi_cache_utils.dandi` |

Where the copies disagreed, the better behaviour was taken, not the most common one: the push retry
keeps the remote-SHA reconciliation from `qualifying-aind-content-ids`, the input handling keeps its
never-fetch-the-recorded-commit fix and its idempotent registration, the cleanliness check keeps the
template's corrected form, and `dist` publishes exactly the files `cache.toml` declares.

Three behaviours are new, because the duplication was hiding the need for them: checkpointing, so a
killed batch keeps what it did; a job timeout, so a hung read cannot burn six hours; and per-branch
image tags, so an environment change can be tested without overwriting `:latest`.
