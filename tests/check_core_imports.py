"""Check what a clean interpreter sees: what gets imported, and what the package offers.

Two properties no ordinary test can observe, because both are about a fresh interpreter's view of
the package rather than about any function's return value:

1. `_config.py` runs as a plain script with nothing but the standard library available. That is
   the pipeline's first step: it parses `cache.toml` with the CI runner's bare `python3`, against
   the sources vendored into the image, before any environment has been built. Running it as a
   script means the package's `__init__.py` never executes, so the rule binds that one file.

2. Importing the package pulls in none of the optional extras, and offers the intended namespace.
   The `:latest` base image installs `[s3,archive]` but not the NWB stack, so a module-level
   `import pynwb` anywhere the import graph reaches would break every cache built on it. The
   accessor modules keep their third-party imports inside the functions that use them, which is
   what makes the plain `from . import api, nwb, s3` in `__init__.py` free.

Run as a script, not under pytest: it has to observe a clean interpreter's `sys.modules`.
"""

import contextlib
import importlib.abc
import io
import pathlib
import runpy
import sys

#: The optional extras. None of these may be imported by importing the package, and none of them
#: may be reachable at all from the module the pipeline runs before an environment exists.
EXTRAS = {
    "boto3",
    "botocore",
    "dandi",
    "h5py",
    "hdmf_zarr",
    "nwbinspector",
    "pynwb",
    "remfile",
    "s3fs",
    "zarr",
}

#: `rich-click` is the distribution's one required dependency, so the command line is imported
#: with everything else. The bootstrap below never reaches it: it runs a module, not the package.
BOOTSTRAP_BLOCKED = EXTRAS | {"click", "rich_click"}

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src" / "dandi_cache_utils"
EXAMPLE_CONFIG = REPOSITORY_ROOT / "docs" / "examples" / "valid-nwb-file-to-number-of-groups" / "cache.toml"


class _Blocker(importlib.abc.MetaPathFinder):
    """Makes the named packages unimportable, whether or not they are installed here."""

    def __init__(self, blocked: set[str], /) -> None:
        self.blocked = blocked

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.blocked:
            raise ImportError(f"{name} is not available before the runner builds its environment")
        return None


def check_the_bootstrap_needs_only_the_standard_library() -> list[str]:
    """Run the pipeline's first step the way the pipeline runs it, with the extras made absent."""
    blocker = _Blocker(BOOTSTRAP_BLOCKED)
    sys.meta_path.insert(0, blocker)
    argv = sys.argv
    sys.argv = ["_config.py", str(EXAMPLE_CONFIG)]
    try:
        # It renders the shell assignments on stdout, which is the pipeline's business, not ours.
        with contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(SOURCE_DIRECTORY / "_config.py"), run_name="__main__")
    except BaseException as error:  # noqa: BLE001 - any failure here is the failure being checked
        if not isinstance(error, SystemExit) or error.code not in (None, 0):
            return [f"the pipeline's first step failed without an environment: {error!r}"]
    finally:
        sys.argv = argv
        sys.meta_path.remove(blocker)
    return []


def check_the_extras_are_not_imported() -> list[str]:
    """Importing the package must not need anything the `:latest` image does not install."""
    try:
        import dandi_cache_utils  # noqa: F401
    except ImportError as error:
        return [f"the package could not be imported with only its required dependencies: {error}"]

    loaded = sorted(EXTRAS & set(sys.modules))
    return [f"importing the package pulled in optional extras: {loaded}"] if loaded else []


def check_the_namespace_is_the_declared_one() -> list[str]:
    """`dandi_cache.<TAB>` must list the API, and only the API."""
    import dandi_cache_utils

    failures = []
    offered = {name for name in dir(dandi_cache_utils) if not name.startswith("_")}
    declared = set(dandi_cache_utils.__all__) - {"__version__"}

    if leaked := sorted(offered - declared):
        failures.append(f"completion on the package offers names outside `__all__`: {leaked}")
    if missing := sorted(declared - offered):
        failures.append(f"completion on the package is missing declared names: {missing}")
    for name in sorted(declared):
        if not hasattr(dandi_cache_utils, name):
            failures.append(f"`{name}` is declared but does not resolve")

    if not isinstance(dandi_cache_utils.__version__, str) or not dandi_cache_utils.__version__:
        failures.append(f"`__version__` did not resolve to a version: {dandi_cache_utils.__version__!r}")

    # The accessor modules are the one place a cache completes on a submodule, so they say what
    # they offer rather than listing `json`, `typing` and the rest of their own imports.
    for module in (dandi_cache_utils.api, dandi_cache_utils.nwb, dandi_cache_utils.s3):
        if sorted(dir(module)) != sorted(module.__all__):
            failures.append(f"completion on `{module.__name__}` offers {sorted(dir(module))}")
    return failures


def main() -> int:
    # In this order: the bootstrap has to run before the package is imported, since importing it
    # would leave the very modules the bootstrap is proving it does not need in `sys.modules`.
    failures = check_the_bootstrap_needs_only_the_standard_library()
    unimportable = check_the_extras_are_not_imported()
    failures += unimportable
    # Nothing can be said about a namespace that does not exist, so stop if the import failed.
    if not unimportable:
        failures += check_the_namespace_is_the_declared_one()
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("OK: the bootstrap needs only the standard library, and the namespace is the declared one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
