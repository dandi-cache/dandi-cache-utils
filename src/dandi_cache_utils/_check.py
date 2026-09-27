"""Static checks over a cache's own operation scripts.

A cache's image build verifies the image and the configuration, but never the one file the cache
actually contributes. So `code/update.py` could name a function this library does not have, and
the build would pass: nothing imports it, and the name is resolved at call time, deep inside the
run. The failure surfaced at the next scheduled update, against the real `derivatives` branch.

That is exactly what happened in the window between a library release and the migrations that
depended on it, which is why this exists. Note what it implies about the check: importing the
module is not enough. `dandi_cache.s3.dandiset_ids` inside a function body is never touched by an
import, so the attribute has to be resolved from the syntax tree instead.
"""

import ast
import importlib
import pathlib
import typing

from . import _config

#: The distribution's own name, and what a cache conventionally aliases it to.
PACKAGE = "dandi_cache_utils"

#: Keyword arguments whose absence is the point rather than an oversight, and why.
#:
#: Removing a parameter makes a stale call a `TypeError` -- but only when the call is reached,
#: which for these caches is the next scheduled run against the real `derivatives` branch. Naming
#: them here moves that to the image build, with an error that says what to do instead.
REMOVED_KEYWORDS = {
    "run_full_rebuild": {
        "limit": (
            "a rebuild publishes the complete cache, so a limit here would truncate it; bound the "
            "work inside `build` instead, using `dataset.limit(arguments.limit)`"
        ),
    },
}


def _aliases(tree: ast.Module, /) -> set[str]:
    """The names this module refers to the library by, whatever it imported it as."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE:
                    names.add(alias.asname or alias.name)
    return names


def _attribute_chain(node: ast.Attribute, /) -> list[str] | None:
    """The dotted name an attribute access spells, or `None` if it is not rooted in a plain name."""
    parts = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return list(reversed(parts))


def referenced_names(source: str, /) -> set[tuple[str, ...]]:
    """Every dotted library name the source refers to, as tuples without the alias."""
    tree = ast.parse(source)
    aliases = _aliases(tree)
    references = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = _attribute_chain(node)
        if chain and chain[0] in aliases:
            references.add(tuple(chain[1:]))

    # `ast.walk` yields the inner attribute of every chain as well as the whole, so `s3` arrives
    # beside `s3.dandiset_ids`. Keep only the most specific: resolving it resolves its prefix too.
    return {
        chain
        for chain in references
        if not any(other != chain and other[: len(chain)] == chain for other in references)
    }


def _called_name(node: ast.Call, /) -> str | None:
    """The bare name a call spells, whether it is `f(...)` or `library.f(...)`."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def removed_keywords(source: str, /) -> list[str]:
    """Calls in the source that pass a keyword argument the library no longer accepts.

    Matching is on the bare function name, so a cache that happened to define its own
    `run_full_rebuild` taking a `limit` would be reported too. That is the safe direction to be
    wrong in, and no cache does.
    """
    problems = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        removed = REMOVED_KEYWORDS.get(_called_name(node) or "")
        if not removed:
            continue
        problems.extend(
            f"line {node.lineno}: `{_called_name(node)}(..., {keyword.arg}=...)` is no longer "
            f"accepted: {removed[keyword.arg]}."
            for keyword in node.keywords
            if keyword.arg in removed
        )
    return sorted(problems)


def unresolved_names(source: str, /, *, library: typing.Any = None) -> list[str]:
    """The library names the source refers to that the installed library does not offer."""
    if library is None:
        library = importlib.import_module(PACKAGE)

    missing = []
    for chain in sorted(referenced_names(source)):
        target = library
        for part in chain:
            if not hasattr(target, part):
                missing.append(".".join(chain))
                break
            target = getattr(target, part)
    return missing


def check_operations(config: _config.CacheConfig, /, *, library: typing.Any = None) -> list[str]:
    """Check every operation script this cache declares; returns one message per problem found.

    Four things, in the order a mistake reaches them: the script exists, it parses, every library
    name it uses is one the installed library actually has, and no call passes an argument the
    library has deliberately stopped accepting.
    """
    problems = []
    directory = config.directory or pathlib.Path.cwd()
    for name, operation in sorted(config.operations.items()):
        path = directory / operation.script
        if not path.is_file():
            problems.append(f"{operation.script}: declared by `[operations.{name}]` but not found.")
            continue

        source = path.read_text()
        try:
            missing = unresolved_names(source, library=library)
        except SyntaxError as error:
            problems.append(f"{operation.script}: does not parse ({error.msg}, line {error.lineno}).")
            continue

        problems.extend(
            f"{operation.script}: uses `{PACKAGE}.{missing_name}`, which this library does not offer."
            for missing_name in missing
        )
        problems.extend(f"{operation.script}: {problem}" for problem in removed_keywords(source))
    return problems
