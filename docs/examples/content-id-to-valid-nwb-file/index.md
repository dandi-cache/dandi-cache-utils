# Several outputs and a second entry point: `content-id-to-valid-nwb-file`

Assesses every not-yet-checked NWB file against the NWB Inspector, and publishes three files keyed
by content ID: the verdict, when it was checked, and the messages behind it.

## The declaration

```{literalinclude} cache.toml
:language: toml
```

Declaring all three outputs is what lets the pipeline publish exactly those to `dist`. The caches
that globbed `derivatives/*.jsonl.gz` instead are the ones that shipped a `testing.jsonl.gz` to
consumers.

## The operation

```{literalinclude} code/update.py
:language: python
```

The per-item work and the side outputs live in a small module beside it, which is the right place
for genuinely per-cache logic:

```{literalinclude} code/_common.py
:language: python
```

## The second entry point

The NWB Inspector itself evolves, so what was recorded has to be re-assessed. That used to mean two
bespoke environment variables threaded through this one cache's shell script. It is a declared
entry point now, run by the same pipeline with `OPERATION=refresh`:

```{literalinclude} code/refresh.py
:language: python
```

## Why these choices

- **`on_failure=dandi_cache.RECORD` with `failure_value=False`.** The opposite of the counting
  cache: a file that cannot be opened or inspected is not valid, so the failure *is* the answer and
  recording it means the item is never retried.
- **`recorded={}` and `write=False` in the refresh.** A refresh reprocesses exactly what an update
  deliberately skips, so it cannot use the recorded mapping as its frontier. Those two arguments
  turn the shared loop into "process this selection and hand back the results", which the caller
  then merges into what is already recorded.
- **{func}`~dandi_cache_utils.runner.select_stale`.** Oldest `checked_at` first, sized so a daily
  run cycles the whole cache about monthly. This is the one selection rule no other cache needs,
  which is why it is a function the operation calls rather than something the runner does by
  default.
