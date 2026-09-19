# Ecosystem health review

A point-in-time review of the code health of the whole organization — all sixteen caches plus `cache-template`, `dandi-cache-utils`, `dandi-cache-action` and `superset` — conducted on 2026-09-19.

It looks for risk that emerges from the *system* rather than from any one repository: convention drift, fragile contracts between caches, duplicated logic, and single points of failure.
Everything is measured against the house pattern that `cache-template` defines, not against generic best practice.

The headline is that the structure is sound and the verification is thin.
Fifteen of sixteen caches are byte-for-byte conformant with the template, every cache is on `dandi-cache-action@v3`, and the dependency graph is declared machine-readably in each `cache.toml`.
What almost nothing checks is the one file each cache actually contributes.


## Summary

Three systemic risks, in order:

1. **`limit` silently truncates the published output of six caches**, and four of those six describe it in their manual-dispatch form as an incremental "cap on new items".
   A maintainer using the documented control can shrink a 414,350-record cache to 500 records.
   Never triggered; fully armed.
2. **The checks that guard a cache's own files never run when those files change.**
   `dandi-cache check-operations` and `dandi-cache dataset-description --check` live in a workflow whose path filter excludes `code/` and `cache.toml`.
3. **No cache has any tests**, and `dandi-cache-action` — consumed by all sixteen — has no CI at all.

Three quick wins, all small:

1. Delete `limit=` from five `run_full_rebuild` call sites.
2. Add `code/**` and `cache.toml` to the build workflow's path filter.
3. Add a `pytest` workflow to `dandi-cache-action`.


## The reference pattern

A conforming cache is sixteen tracked files.

| Concern | House pattern |
|---|---|
| Declaration | `cache.toml` — name, `[[inputs]]` with a `format`, `[operations]`, `[description]` |
| Logic | `code/update.py` and nothing else; everything shared is imported from `dandi_cache_utils` |
| Environment | `containers/Dockerfile` built `FROM` the base image, plus an unconditional install of `envs/pyproject.toml` |
| Orchestration | `update.yml` carries only the schedule and the dispatch inputs; the rest is `dandi-cache-action@v3` |
| Publication | `derivatives` holds JSON Lines, `dist` holds the compressed copy, `main` holds the code |
| Provenance | every run writes a `[DATALAD RUNCMD]` commit naming the code SHA and the image digest |

The template's own gaps matter more than any single cache's, because they are why the same issue recurs everywhere:
it specifies no data-quality gate, it says nothing about `limit` meaning different things in the two execution models, it ships no test scaffold, and it has no `CODEOWNERS`.


## The graph

Sixteen caches, seventeen edges, maximum depth five, no cycles.
The edges are derived from the `[[inputs]]` tables rather than guessed from code.

```text
content-id-to-dandiset-paths (414,350)          root, reads S3 directly
  └─ content-id-to-usage-dandiset-path (413,099)        fan-out 4
       ├─ usage-dandiset-path-to-asset-size
       ├─ dandiset-id-to-total-size
       └─ content-id-to-nwb-file (229,867)              fan-out 2
            └─ content-id-to-valid-nwb-file             fan-out 8
                 ├─ six × valid-nwb-file-to-*
                 ├─ qualifying-lfp-content-ids
                 └──── qualifying-aind-content-ids      depth 5
```

`content-id-to-valid-nwb-file` is the hub: ten of the sixteen caches sit downstream of it.
`dandiset-id-to-title` and `dandiset-id-to-number-of-assets` read the bucket directly and feed nothing.

The ten caches with no internal consumer are the organization's products, consumed by people outside it.
None of them is an orphan and none should be retired.


## Health matrix

Each cache scored zero to three against the house pattern.
`fan` is the number of internal consumers.

| cache | fan | Tmpl | Contract | Idemp | Failure | DQ | Obs | Test | Deps | Sec | Docs | Dead | Vel |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| content-id-to-dandiset-paths | 1 | 3 | 2 | 1 | 1 | 1 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |
| content-id-to-usage-dandiset-path | 4 | 3 | 2 | 1 | 1 | 1 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |
| content-id-to-nwb-file | 2 | 3 | 2 | 1 | 1 | 0 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |
| content-id-to-valid-nwb-file | 8 | 3 | 2 | 3 | 3 | 0 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |
| dandiset-id-to-number-of-assets | 0 | 3 | 2 | 1 | 1 | 1 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |
| dandiset-id-to-title | 0 | 3 | 2 | 1 | 1 | 1 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |
| dandiset-id-to-total-size | 0 | 3 | 2 | 1 | 1 | 0 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |
| usage-dandiset-path-to-asset-size | 1 | 3 | 2 | 3 | 1 | 0 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |
| qualifying-lfp-content-ids | 1 | 3 | 2 | 3 | 3 | 0 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |
| qualifying-aind-content-ids | 0 | 2 | 2 | 3 | 3 | 0 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |
| six × valid-nwb-file-to-* | 0 | 3 | 2 | 3 | 3 | 0 | 3 | 0 | 2 | 2 | 2 | 3 | 3 |

Column means: template conformance 2.94, observability 3.00, dead code 3.00, change velocity 3.00, idempotency 2.25, failure handling 2.12, contract definition 2.00, dependency health 2.00, secrets 2.00, documentation 2.00, data-quality gates 0.25, testing 0.00.

The shape of the risk is that last pair.
Everything structural scores high and everything that would catch a mistake scores near zero.


## Finding 1: `limit` means opposite things in the two execution models

Two runners take a parameter of the same name through the same workflow input.

`run_incremental_update` caps the new batch and rewrites the full prior mapping, in `src/dandi_cache_utils/_runner.py`:

```python
batch = list(batch) if candidates is None else select_new(candidates, records, limit=limit)
```

`run_full_rebuild` caps the entire published file:

```python
records = list(build())
if limit is not None:
    records = list(itertools.islice(records, limit))

file_path = dataset.write_output_records(records, output)
```

The first is safe and the second is destructive, and nothing in the template, the workflow or the dispatch form distinguishes them.

Six caches truncate their published output when given a `limit`:

| cache | call site | description shown to the operator |
|---|---|---|
| content-id-to-dandiset-paths | `code/update.py:80` | "Cap on the newly valid content IDs… default of 500" — wrong |
| dandiset-id-to-title | `code/update.py:54` | "Cap on the newly valid content IDs… default of 500" — wrong |
| dandiset-id-to-number-of-assets | `code/update.py:54` | "Cap on the newly valid content IDs… default of 500" — wrong |
| dandiset-id-to-total-size | `code/update.py:69` | "Cap on the newly valid content IDs… default of 500" — wrong |
| content-id-to-nwb-file | `code/update.py:28` | "Cap on the records written… recompute the full subset" — honest |
| content-id-to-usage-dandiset-path | `code/update.py:83` | "Cap on the entries taken from each category" — honest |

The four marked wrong carry text copy-pasted from the `valid-nwb-file-*` family.
It is wrong in wording, wrong in semantics, and cites a default of 500 that their `cache.toml` does not declare.
`content-id-to-dandiset-paths` is the root of the whole chain.

Testing mode is not a route to this, because it redirects every write to `testing_`-prefixed files.
The danger is a `limit` supplied without `testing`, which the dispatch form allows since the two inputs are independent.

It has never fired.
Fourteen published revisions of each of the six were checked and every series is monotonic — `content-id-to-dandiset-paths` runs 403,933 → 414,350 across the window with no decrease.

The fix belongs here rather than in six repositories: `run_full_rebuild` should reject a `limit` unless `dataset.testing` is set, and the five pass-through call sites should drop the argument.


## Finding 2: the v3 check never runs when the file it checks changes

`dandi-cache check-operations` exists to validate `code/update.py`, and `dandi-cache dataset-description --check` exists to validate `cache.toml` against the committed `dataset_description.json`.
Both run from `build-and-publish-image`, which is invoked only by `build_and_upload_docker_image.yml`, whose `push` and `pull_request` triggers are path-filtered:

```yaml
    paths:
      - "envs/pyproject.toml"
      - "containers/**"
      - ".github/workflows/build_and_upload_docker_image.yml"
```

A cache's other workflow, `update.yml`, is `schedule` and `workflow_dispatch` only.
So no workflow in any cache is triggered by a change to `code/update.py` or `cache.toml` — the two files the checks were written for.
This was confirmed across all sixteen.

It is observable rather than only inferable.
The `AGENTS.md` commit of 2026-09-17 in `content-id-to-nwb-file` produced no build run at all; the newest run on that repository is still the earlier Dockerfile commit.

There is real history of logic-only commits that would have bypassed the check had it existed at the time, including `qualifying-aind-content-ids` at `e3ebd568` and `64458346`, and `content-id-to-valid-nwb-file` at `56e71cb`.

The fix is to add `code/**` and `cache.toml` to the path filter in the template and sweep the sixteen.


## Finding 3: rebuild caches fail silent, incremental caches fail loud

`run_incremental_update` gives a failed item a named per-stage error log, counts it, and leaves it unrecorded for a later run to retry.
`run_full_rebuild` offers no equivalent, so each rebuild cache hand-rolls its own handling — and all of them drop failures silently.

In `dandiset-id-to-title`, unreadable metadata becomes `None` and is filtered out:

```python
title = None if metadata is None else metadata.get("name")
return None if title is None else (dandiset_id, title)
...
titles = dict(result for result in results if result)
```

In `usage-dandiset-path-to-asset-size`, a missing manifest returns an empty mapping.
Neither writes an error log, and neither counts the loss.

Because these caches seed from `read_output_lookup()`, a Dandiset that becomes permanently unreadable keeps its last known value forever with no signal anywhere.
That is the textbook silent-stale-data failure, and it affects the three rebuild caches that read the bucket directly.

The rebuild model should get the same error-log contract the incremental model already has.


## Finding 4: the contract between caches is declared but never versioned

`cache.toml` declares each input's `format`, and `CacheDataset.read_input` makes inputs required by default, which is a genuine and well-chosen guard:

```python
def read_input(self, name: str | None = None, /, *, required: bool = True) -> dict | list | set:
    """Read an upstream cache in the shape its `cache.toml` entry declares.

    Inputs are required by default: an input subdataset that failed to check out would
    otherwise be read as empty, and the run would happily publish an empty cache over a good one.
    """
```

No cache opts out of it.
But `format` is a parse hint, not a schema, and nothing versions or validates the content shape.

This has already cost real work, twice, on the same edge.
`qualifying-aind-content-ids` carries `f14dfdcf`, "Load qualifying LFP content IDs from new `[id, qualifies]` pairs", and `e3ebd568`, "Read qualifying-lfp-content-ids upstream file as lookup-dict".
Both are a downstream cache chasing a shape change it learned about after the fact.

Ranked by criticality against lack of enforcement, the exposed edges are `content-id-to-valid-nwb-file` to its eight consumers, and `content-id-to-nwb-file` to its two.


## Finding 5: schedule order contradicts dependency order

Comparing the declared crons against the graph, eight of the seventeen edges have a cache scheduled at or before the cache it reads.

| cache | first run | its input's first run |
|---|---|---|
| content-id-to-nwb-file | 03:00 | 04:00 |
| usage-dandiset-path-to-asset-size | 00:00 | 04:00 |
| qualifying-lfp-content-ids | 00:00 | 06:00 and 03:00 |
| valid-nwb-file-to-number-of-datasets | 06:00 | 06:00 |
| valid-nwb-file-to-number-of-groups | 06:00 | 06:00 |
| valid-nwb-file-to-sackin-index | 06:00 | 06:00 |
| qualifying-aind-content-ids | 06:00 | 06:00 |

This costs latency, not correctness.
Every one of these caches is incremental or seeded, so a run that starts early simply picks the work up on its next pass.
But with a five-deep chain and GitHub's habitual two-to-five-hour scheduler lag, a newly published asset takes days to reach `qualifying-aind-content-ids`.

Staggering the crons by graph depth is a configuration change and costs nothing.


## Finding 6: duplication is concentrated in one family

Across forty-eight non-trivial functions in the sixteen caches, AST-normalized comparison finds the duplication in a single cluster.

The six `valid-nwb-file-to-*` `main()` bodies are between 88% and 99% identical; `number-of-datasets` against `number-of-groups` scores 0.989 and differs by four lines.
A shared helper in this library would collapse six copies into six one-line calls.

`dandiset-id-to-title` and `dandiset-id-to-number-of-assets` share a `build()` at 0.981 — the same list-concurrently, seed, merge shape, including the same silently dropped failures described in finding 3.

The `assess()` functions of `qualifying-aind-content-ids` and `qualifying-lfp-content-ids` score 0.966 but carry deliberately opposite failure policies: one records a failure as `False` so it is never retried, the other skips it so it is.
Near-identical code with divergent semantics is the dangerous kind of duplication, because a copy-pasted fix silently flips the policy.
Both call sites deserve a comment saying so.


## Finding 7: verification sits in the one repository nothing runs at runtime

This library is well covered: 111 test functions over 1,765 lines, a matrix across Python 3.11, 3.12 and 3.13, and a version-check gate on every pull request.
It even pins the HDF5 link-policy default, which neutralizes what would otherwise be a live drift risk across the five tree-walking caches:

```python
assert nwb.walk_hdf5_group(awkward_hdf5_file).links == nwb.LINKS_SKIPPED
```

`dandi-cache-action` has no CI workflow at all.
Its only workflow is `prepare_release.yml`, and `tests/test_actions.py` runs solely from a `.pre-commit-config.yaml` hook — so an edit made through the GitHub web interface, or by a contributor without pre-commit installed, never runs it.
That repository is the composite action all sixteen caches call.

No cache has any tests.


## Finding 8: one maintainer, one credential

Every commit across all twenty repositories is authored by one person, no repository has a `CODEOWNERS` file, and `dependabot.yml` hardcodes a single reviewer and assignee.

All sixteen caches push using one organization-wide token, `secrets._GITHUB_API_KEY`.
Its expiry breaks the entire ecosystem at the same moment.
No hardcoded credentials exist anywhere; that was scanned for and is clean.

One smaller note: each cache's `update.yml` declares `permissions: contents: read`, yet the pipeline pushes to `derivatives` and `dist`.
The push works because the token carries its own scope, which means the declared permissions block does not describe the access the job actually has.


## Finding 9: there are no data-quality gates

There is no row-count floor, no freshness assertion, no schema check and no shrink guard anywhere in the library.
`run_full_rebuild` will write an empty list without complaint.

The only thing resembling a gate is a hand-rolled "the upstream returned nothing" raise present in four of the sixteen caches, of which `dandiset-id-to-title` is the clearest:

```python
if not titles:
    message = (
        f"No Dandiset titles were found under `s3://{dandi_cache.s3.BUCKET}/"
        f"{dandi_cache.s3.DANDISETS_PREFIX}`. The archive bucket may be unreachable or its "
        "layout may have changed."
    )
    raise RuntimeError(message)
```

That this is a per-cache habit rather than a library feature is the finding.
It is a gap in the template, which is why the fix belongs here and not in sixteen pull requests.


## Per-cache notes

Only genuine deviations are listed; the matrix covers the rest.

`qualifying-aind-content-ids` is the only structural non-conformer.
It is missing `.github/ISSUE_TEMPLATE/config.yml`, `.github/ISSUE_TEMPLATE/issue.yml` and `envs/.python-version`, and it is the only cache with no `authors` in its `[description]`.
It predates the template, which explains the divergence without excusing it.

`content-id-to-valid-nwb-file` carries `code/refresh.py` and `.github/workflows/refresh.yml` beyond the template, and declares three outputs.
Both are a declared second operation working as designed.

Three caches — `content-id-to-dandiset-paths`, `qualifying-aind-content-ids` and `usage-dandiset-path-to-asset-size` — omit `labels: []` from `dependabot.yml`.
Cosmetic.

`valid-nwb-file-to-number-of-groups` is the only tree-walking cache that documents no link policy where four siblings do.
Its behaviour is identical to theirs because the library default is pinned by test, so this is a documentation inconsistency rather than a risk.


## What to do

Fix now, because each sits on a critical path with a silent failure mode and no test:

1. Remove the `limit` truncation path — five one-line deletions plus a guard in `run_full_rebuild`.
2. Add `code/**` and `cache.toml` to the build workflow's path filter, in the template and then across the sixteen.
3. Correct the four misleading dispatch descriptions, which folds into the same sweep.

Fix systemically, in this library or the template, so the fix lands everywhere at once:

4. A shrink guard in `run_full_rebuild` that refuses to write an output dramatically smaller than the previous one unless explicitly overridden.
   This retires the whole class of problem that finding 1 is one instance of.
5. A test workflow for `dandi-cache-action`.
6. An error-log contract for the rebuild model matching the one the incremental model has.
7. A shared helper for the six duplicated tree-metric `main()` bodies.
8. Crons staggered by graph depth.
9. A versioned published contract — a `schema_version` in `cache.toml`, checked on read.
   This is the largest design question here, and the two `qualifying-aind-content-ids` incidents are the evidence that it is needed.

Accept and document:

- Floating `:latest` and `:nwb` base tags, with no cache pinning by digest.
  This is deliberate — the fan-out rebuild plus the digest recorded in provenance is the design — but it does mean a bad base image reaches all sixteen caches at once.
- The ten caches with no internal consumer, which are products rather than orphans.
- The implicit link policy in `valid-nwb-file-to-number-of-groups`.

Nothing should be retired.
There are no dead pipelines, no dead workflow branches and no stale configuration.

The single highest-leverage action that is not code is adding `CODEOWNERS` and a second reviewer on the shared layer, because this library and `dandi-cache-action` are where one mistake reaches sixteen repositories at once.


## Method and limits

Scanned mechanically at full coverage: all twenty repositories synchronized to their current `main`; every `cache.toml` parsed to build the graph; AST-based duplicate detection over all forty-eight non-trivial functions; a `git ls-files` structural diff of all sixteen caches against the template; cron parsing and depth-ordering across all seventeen edges; a credential scan; and workflow-trigger enumeration for every workflow file in the organization.

Read in depth: the template's `cache.toml`, `code/update.py`, `update.yml` and `Dockerfile`; `dandi-cache-action/action.yml`; this library's `_runner.py`, `_dataset.py` and `_jsonl.py`; and the `update.py` of four caches chosen to cover both runner models and the highest fan-out.

Verified empirically rather than inferred: that finding 1 has never fired, from fourteen published revisions of each of the six affected caches; that finding 2's path filter really does gate the build, from a commit that produced no run; and that the link-policy default is test-pinned.

Not determined:

- Whether a job timeout fires the failure notification.
  A hung network read is the scenario `timeout-minutes: 330` exists for, and if a timeout is treated as a cancellation rather than a failure then that scenario notifies nobody.
  This was not tested live and is worth a deliberate experiment.
- Run success and failure rates over time, which were sampled but not aggregated.
- Anything requiring the DANDI API, which was unreachable from the audit environment.

Confidence is high on findings 1, 2, 5, 7 and 8, all of which were observed directly in configuration or history.
It is medium on findings 3 and 4, which are behavioural reasoning from read code with no live reproduction.
The severity assigned to finding 9 is a judgement rather than a measurement.
