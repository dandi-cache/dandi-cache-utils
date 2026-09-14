"""Check what the core imports, and what it offers to completion.

Two properties that no ordinary test can observe, because both are about a clean interpreter's
view of the package rather than about any function's return value:

1. Importing the core pulls in nothing outside the standard library. `bin/update_pipeline.sh`
   parses `cache.toml` with the CI runner's bare `python3`, before any environment has been
   built. If anything in the core grows an import of boto3, h5py or pynwb, the orchestration
   stops working before the run even starts.

2. The public namespace is the intended one. Every cache's `code/update.py` is written against
   `dandi_cache.<TAB>`, so what completion lists is the API as far as anyone writing a cache is
   concerned. Left alone, a package gets this backwards: the implementation modules bound by the
   re-exports (`cli`, `config`, `dataset`, ...) show up, the lazily bound accessors (`nwb`, `s3`,
   `api`) do not, and each module offers its own imports (`dataclasses`, `pathlib`, `typing`)
   alongside its functions. `__dir__` fixes that, and this checks it stayed fixed.

Run as a script, not under pytest: it has to observe a clean interpreter's `sys.modules`, and it
deliberately never touches `nwb`, `s3` or `api`, whose dependencies it is asserting are absent.
"""

import ast
import pathlib
import sys

#: `click`/`rich_click` are here with the heavy scientific stack for the same reason: the command
#: line is the only part of the distribution that needs them, and the pipeline's first step runs
#: without any of it.
BLOCKED = {
    "boto3",
    "botocore",
    "click",
    "dandi",
    "h5py",
    "hdmf_zarr",
    "nwbinspector",
    "pynwb",
    "remfile",
    "rich_click",
    "s3fs",
    "zarr",
}

#: Bound as attributes of the package by the re-exports in `__init__.py`, and hidden from
#: completion on purpose: everything they define is re-exported, so naming them is a detour.
IMPLEMENTATION_MODULES = {"cli", "config", "dandi", "dataset", "jsonl", "logs", "runner"}

#: The modules a cache reaches for by name, which are bound only on first use.
ACCESSOR_MODULES = {"api", "nwb", "s3"}

SOURCE_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "src" / "dandi_cache_utils"


def check_standard_library_only() -> list[str]:
    """The core must not drag in a third-party package on import."""
    import dandi_cache_utils  # noqa: F401
    import dandi_cache_utils.cli  # noqa: F401
    import dandi_cache_utils.config  # noqa: F401
    import dandi_cache_utils.dataset  # noqa: F401
    import dandi_cache_utils.jsonl  # noqa: F401
    import dandi_cache_utils.logs  # noqa: F401
    import dandi_cache_utils.runner  # noqa: F401

    loaded = sorted(BLOCKED & set(sys.modules))
    return [f"the core imported third-party modules: {loaded}"] if loaded else []


def check_package_namespace() -> list[str]:
    """`dandi_cache.<TAB>` must list the API, and only the API.

    This runs after the implementation modules have been imported by name above, which is exactly
    the state in which they would leak, so it is the strongest point to look.
    """
    import dandi_cache_utils

    failures = []
    offered = {name for name in dir(dandi_cache_utils) if not name.startswith("_")}
    expected = set(dandi_cache_utils.__all__) - {"__version__"} | ACCESSOR_MODULES

    if leaked := sorted(offered & IMPLEMENTATION_MODULES):
        failures.append(f"completion on the package offers implementation modules: {leaked}")
    if missing := sorted(expected - offered):
        failures.append(f"completion on the package is missing public names: {missing}")
    if extra := sorted(offered - expected - IMPLEMENTATION_MODULES):
        failures.append(f"completion on the package offers unintended names: {extra}")

    # An advertised name that does not resolve is worse than a hidden one. The accessors are
    # excluded deliberately: resolving them would import the very packages checked for above.
    for name in sorted(expected - ACCESSOR_MODULES):
        if not hasattr(dandi_cache_utils, name):
            failures.append(f"`{name}` is advertised but does not resolve")
    if eager := sorted(ACCESSOR_MODULES & {name for name in vars(dandi_cache_utils)}):
        failures.append(f"accessor modules were imported eagerly: {eager}")

    try:
        dandi_cache_utils.definitely_not_a_real_name
    except AttributeError:
        pass
    else:
        failures.append("an unknown attribute resolved instead of raising AttributeError")

    # `__version__` is served lazily from `pyproject.toml`, the one place it is declared.
    if not isinstance(dandi_cache_utils.__version__, str) or not dandi_cache_utils.__version__:
        failures.append(f"`__version__` did not resolve to a version: {dandi_cache_utils.__version__!r}")

    import dandi_cache_utils.dandi

    if sorted(dir(dandi_cache_utils.dandi)) != sorted(ACCESSOR_MODULES):
        failures.append(f"completion on `dandi` offers {sorted(dir(dandi_cache_utils.dandi))}")
    return failures


def _public_definitions(tree: ast.Module) -> list[str]:
    """The public names a module defines itself, which is what its `__all__` should hold.

    Imported names are excluded on purpose: a module's imports are the noise that `__dir__` is
    there to keep out of its completion listing.
    """
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name) and not t.id.startswith("_")]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                names.append(node.target.id)
    return names


def check_accessor_modules() -> list[str]:
    """`dandi_cache.nwb.<TAB>` must list what the module defines, not what it imports.

    Read rather than imported: these modules are the ones whose dependencies this script exists to
    prove absent, so it must not import them to inspect them.
    """
    failures = []
    for name in sorted(ACCESSOR_MODULES):
        path = SOURCE_DIRECTORY / "dandi" / f"{name}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        declared = None
        defines_dir = False
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            ):
                declared = [element.value for element in node.value.elts]
            elif isinstance(node, ast.FunctionDef) and node.name == "__dir__":
                defines_dir = True

        if declared is None:
            failures.append(f"{path.name} does not declare `__all__`")
            continue
        if not defines_dir:
            failures.append(f"{path.name} declares `__all__` but no `__dir__`, so completion ignores it")
        defined = _public_definitions(tree)
        if undeclared := sorted(set(defined) - set(declared)):
            failures.append(f"{path.name} defines public names missing from `__all__`: {undeclared}")
        if phantom := sorted(set(declared) - set(defined)):
            failures.append(f"{path.name} declares names it does not define: {phantom}")
    return failures


def main() -> int:
    failures = check_standard_library_only() + check_package_namespace() + check_accessor_modules()
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("OK: the core is standard-library only and its public namespace is the intended one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
