# Agent instructions

The organization's conventions, as set out in [`CodyCBakerPhD/historia`](https://github.com/CodyCBakerPhD/historia/blob/main/AGENTS.md), with the few places this repository differs noted as such.

## Commits and PRs

- Always run `pre-commit` before committing and pushing changes.
- Always link PRs to issues when possible.
- PR titles should be human-readable and in the past tense.
  They should NOT use conventional commit style.
- Every commit must include a `Co-Authored-By` trailer identifying the tool and the model that wrote it.

## Versioning and changelog

- Bump the version in `pyproject.toml` once per PR when any file under `src/` or `pyproject.toml` itself changes.
  That is the only place to edit it.
  Do NOT bump for changes that are purely CI/workflow, documentation, or configuration.
- The actions a cache's CI calls live in [`dandi-cache-action`](https://github.com/dandi-cache/dandi-cache-action) and are versioned by their own interface, so a release here never changes them.
- The changelog follows the version.
  If the PR does not bump `pyproject.toml`, it needs no `CHANGELOG.md` entry either.
- Otherwise add a short entry to the `## Upcoming` section of `CHANGELOG.md` under the appropriate subsection (`### 🚀 Enhancement`, `### 🐛 Bug Fix`, `### 📝 Documentation`, `### 🔩 Dependency Updates`, `### 🏠 Internal`), with the PR link at the end in the format `([#N](https://github.com/dandi-cache/dandi-cache-utils/pull/N))`.

## Code style

- Require keyword-only arguments `(*, ...)` for multi-input functions.
  For any function with exactly one caller-supplied parameter (excluding `self` and `cls`), require positional-only usage with the `/` designator.
- Always add new imports at the top of the file.
  **Two exceptions here**, both load-bearing: a local import that avoids a circular dependency, and an extra's import placed inside the function that uses it (see below).
- For external dependencies, use the full module import style (`import xyz; xyz.abc`) rather than `from xyz import abc`.
- For internal imports, always use relative style (`from .foo import bar`).
- Prefer assigning return values to named locals before `return` when it improves readability and debugger breakpoint placement.
- Avoid excessive em-dashes, colons, and semicolons in written text such as documentation.
  Prefer breaking into separate, shorter sentences instead.
- In Markdown, give each sentence its own line rather than wrapping prose to a fixed width.
  A reworded sentence is then a one-line diff instead of a reflowed paragraph, which is what makes a prose suggestion on a pull request reviewable.
- Favor one-word names for CLI flags, mapped onto longer, more explicit keyword arguments at the API level.
- Keep inline comments sparse.
  Explain non-obvious "why", never "what".

## Tests

- Ensure tests pass before pushing.
- Assertion style: actual on the left, expected on the right.
- Always mark AI-generated tests with the `ai_generated` pytest marker.
- Use `pytest.mark.parametrize` wherever it reduces duplication.
- Never import private API (a leading underscore) in tests.
  Import what is publicly exposed through `__init__.py`.
- When monkeypatching internal imports, target the importing module's binding (`foo.baz`), not the original definition module (`foo._bar.baz`).

## Module and API conventions

- Implementation modules carry a leading underscore (`_runner.py`, `_config.py`).
  The public API is the flat namespace `__init__.py` re-exports, so nothing needs hiding from completion and moving a function between modules is not a breaking change.
- `__init__.py` holds imports and `__all__` and nothing else: no `__getattr__`, no `__dir__`, no branching.
  If a name has to be bound lazily to keep an import out, the import belongs inside the function that needs it instead.
- `__all__` lists the public submodules alongside the names, and never a private name.
- The three archive modules (`api`, `nwb`, `s3`) are public because a cache names them directly.
  They declare `__all__` and a `__dir__` that returns it, since they are the only modules anyone completes on.
- Do not add compatibility aliases when renaming.
  Update the call sites.

## The one rule about imports

An extra's third-party import goes inside the function that uses it, never at the top of a module.
The `:latest` base image installs `[s3,archive]` and not the NWB stack, so a module-level `import pynwb` anywhere in the import graph would break every cache built on it.

`_config.py` is stricter: it imports nothing outside the standard library, at the top of the file or anywhere else.
The pipeline runs it as a plain script with the CI runner's bare `python3` to read `cache.toml`, before any environment exists.
Running it as a script rather than importing it means `__init__.py` never executes, so the rule binds that one file rather than the whole package.

`tests/check_core_imports.py` enforces both, and that the namespace `dir()` offers is the one `__all__` declares.
