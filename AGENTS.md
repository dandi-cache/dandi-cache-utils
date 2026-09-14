# Agent instructions

The organization's conventions, as set out in
[`CodyCBakerPhD/historia`](https://github.com/CodyCBakerPhD/historia/blob/main/AGENTS.md), with the
few places this repository differs noted as such.

## Commits and PRs

- Always run `pre-commit` before committing and pushing changes.
- Always link PRs to issues when possible.
- PR titles should be human-readable and in the past tense. They should NOT use conventional commit
  style.
- Every commit must include a `Co-Authored-By` trailer identifying the tool and the model that
  wrote it.

## Versioning and changelog

- Bump the version in `pyproject.toml` once per PR when any file under `src/` or `pyproject.toml`
  itself changes. That is the only place to edit it. Do NOT bump for changes that are purely
  CI/workflow, documentation, or configuration.
- The actions a cache's CI calls live in
  [`dandi-cache-action`](https://github.com/dandi-cache/dandi-cache-action) and are versioned by
  their own interface, so a release here never changes them.
- The changelog follows the version. If the PR does not bump `pyproject.toml`, it needs no
  `CHANGELOG.md` entry either.
- Otherwise add a short entry to the `## Upcoming` section of `CHANGELOG.md` under the appropriate
  subsection (`### 🚀 Enhancement`, `### 🐛 Bug Fix`, `### 📝 Documentation`,
  `### 🔩 Dependency Updates`, `### 🏠 Internal`), with the PR link at the end in the format
  `([#N](https://github.com/dandi-cache/dandi-cache-utils/pull/N))`.

## Code style

- Require keyword-only arguments `(*, ...)` for multi-input functions. For any function with
  exactly one caller-supplied parameter (excluding `self` and `cls`), require positional-only usage
  with the `/` designator.
- Always add new imports at the top of the file. **Two exceptions here**, both load-bearing: a
  local import that avoids a circular dependency, and the deliberately deferred imports that keep
  the core standard-library only (see below).
- For external dependencies, use the full module import style (`import xyz; xyz.abc`) rather than
  `from xyz import abc`.
- For internal imports, always use relative style (`from .foo import bar`).
- Prefer assigning return values to named locals before `return` when it improves readability and
  debugger breakpoint placement.
- Avoid excessive em-dashes, colons, and semicolons in written text such as documentation. Prefer
  breaking into separate, shorter sentences instead.
- Favor one-word names for CLI flags, mapped onto longer, more explicit keyword arguments at the
  API level.
- Keep inline comments sparse. Explain non-obvious "why", never "what".

## Tests

- Ensure tests pass before pushing.
- Assertion style: actual on the left, expected on the right.
- Always mark AI-generated tests with the `ai_generated` pytest marker.
- Use `pytest.mark.parametrize` wherever it reduces duplication.
- Never import private API (a leading underscore) in tests. Import what is publicly exposed through
  `__init__.py`.
- When monkeypatching internal imports, target the importing module's binding (`foo.baz`), not the
  original definition module (`foo._bar.baz`).

## Module and API conventions

- Every module declares `__all__` and a `__dir__` that returns it. `__all__` lists what the module
  defines, never what it imports, so completion on a module offers its API rather than its
  dependencies. `tests/check_core_imports.py` enforces this for every module in the package.
- Never expose private names in any module's `__all__`.
- Never include code other than imports, `__all__`, simple import errors, or magic overrides in any
  `__init__.py`. **The magic overrides here do real work**, which is the one place this repository
  departs from the letter of the rule: `__getattr__` binds `nwb`, `s3`, `api`, `dandi_cache_cli`
  and `__version__` on first use, and `__dir__` declares the public surface. Both exist for the
  same reason as the deferred imports above.
- Do not add compatibility aliases when renaming. Update the call sites.

## The rule behind the exceptions

`bin`-less orchestration: the pipeline script parses a cache's `cache.toml` with the CI runner's
bare `python3`, before any environment exists. So importing `dandi_cache_utils` must pull in
nothing outside the standard library, boto3, h5py, pynwb and rich-click included.
`tests/check_core_imports.py` enforces it, along with the public namespace `__dir__` declares.
Anything that would break either belongs behind a deferred import, not at the top of a module.
