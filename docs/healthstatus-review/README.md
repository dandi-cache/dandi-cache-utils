# Ecosystem health checks

Each file in this directory is one point-in-time audit of the whole DANDI Cache organization, named for the date it was conducted.
Reviews are added rather than overwritten, so the direction of travel stays visible.
A finding that survives three reviews is a different problem from one that appeared last week.

A review covers every repository in the organization, not this library alone.
It looks for risk that emerges from the system rather than from any single repository, and it measures everything against the house pattern that `cache-template` defines rather than against generic best practice.


## Reviews

| Date | Review | Scope | Headline |
|---|---|---|---|
| 2026-09-19 | [Ecosystem health review](2026-09-19.md) | 16 caches, `cache-template`, `dandi-cache-utils`, `dandi-cache-action`, `superset` | Structure is sound, verification is thin |

The 2026-09-19 review is the first, so nothing in it is yet a trend.


## Current standing

From the most recent review.
Tick an item off here when it lands, and note the pull request that closed it.

### Fix now

| Item | Where | Status |
|---|---|---|
| `limit` truncates the published output of six full-rebuild caches | five call sites, plus a guard in `run_full_rebuild` | open |
| `check-operations` and `dataset-description --check` never run when `code/` or `cache.toml` changes | build workflow path filter, template then sixteen caches | open |
| Four dispatch descriptions describe incremental behaviour on caches that truncate | four cache `update.yml` files | open |

### Fix systemically

| Item | Where | Status |
|---|---|---|
| A shrink guard, so no rebuild can publish a drastically smaller cache unprompted | `run_full_rebuild` | open |
| A test workflow | `dandi-cache-action` | open |
| An error-log contract for the rebuild model, matching the incremental one | this library | open |
| A shared helper for the six duplicated tree-metric `main()` bodies | this library | open |
| Crons staggered by graph depth | sixteen cache `update.yml` files | open |
| A versioned published contract between caches | `cache.toml` and the readers | open |

### Accepted

| Item | Why |
|---|---|
| Floating `:latest` and `:nwb` base tags, no digest pinning | deliberate, since the fan-out rebuild plus the digest in provenance is the design |
| Ten caches with no internal consumer | they are the organization's products, not orphans |
| The implicit link policy in `valid-nwb-file-to-number-of-groups` | the library default it relies on is pinned by test |

Nothing has been retired, and the 2026-09-19 review found nothing that should be.


## What a review covers

Every cache is scored zero to three on twelve dimensions, so that successive reviews can be compared directly:
template conformance, contract definition, idempotency and reruns, failure handling, data-quality gates, observability, testing, dependency health, secrets and access, ownership and documentation, dead code and config, and change velocity.

Alongside the per-cache scores, a review builds the dependency graph from the `[[inputs]]` tables in each `cache.toml` and reports fan-in, fan-out, depth, hubs and cycles.
That graph is what turns a local finding into a blast radius.


## Running the next one

Measure against `cache-template` first and generic best practice second.
A cache that does not use some pattern is not a finding if the template does not use it either, but a gap in the template is a finding about every cache at once.

Prefer breadth before depth.
Scan all twenty repositories mechanically, then read in full only the handful the graph says carry the most risk, and say which ones those were.

Cite a path and a line for every claim, and mark an inference as an inference.
The 2026-09-19 review records the findings it downgraded after checking them, which is worth repeating: knowing what was investigated and dismissed is what stops the next review from re-litigating it.

Prefer a fix that lands in one place.
When the same issue appears in five or more caches, the recommendation is a change to this library or to the template plus a migration, not five separate issues.
