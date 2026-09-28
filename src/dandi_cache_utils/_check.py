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
import inspect
import pathlib
import typing

from . import _config

#: The distribution's own name, and what a cache conventionally aliases it to.
PACKAGE = "dandi_cache_utils"


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


def _accepted_keywords(target: typing.Any, /) -> set[str] | None:
    """The keyword names a callable accepts, or `None` when it accepts anything (or is unreadable)."""
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):  # A builtin or C callable has no signature to read.
        return None

    parameters = signature.parameters.values()
    if any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters):
        return None
    return {
        parameter.name
        for parameter in parameters
        if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    }


def unaccepted_keywords(source: str, /, *, library: typing.Any = None) -> list[str]:
    """Calls on a library function that pass a keyword its signature does not accept.

    Read from the installed signatures rather than from a list of known removals, so there is
    nothing to keep in step: a parameter this library drops, renames or never had is reported the
    same way, and a misspelled keyword is caught as well.

    A stale keyword is already a `TypeError` -- but only once the call is reached, which for these
    caches is the next scheduled run against the real `derivatives` branch. Reading it from the
    syntax tree is what moves that to the image build.
    """
    if library is None:
        library = importlib.import_module(PACKAGE)

    tree = ast.parse(source)
    aliases = _aliases(tree)
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        chain = _attribute_chain(node.func)
        if not chain or chain[0] not in aliases:
            continue

        target = library
        for part in chain[1:]:
            target = getattr(target, part, None)
        # A name the library does not offer at all is `unresolved_names`' to report, not this one.
        if target is None or not callable(target):
            continue

        accepted = _accepted_keywords(target)
        if accepted is None:
            continue

        dotted = ".".join(chain[1:])
        problems.extend(
            f"line {node.lineno}: `{dotted}()` does not accept `{keyword.arg}`."
            for keyword in node.keywords
            # `**kwargs` at the call site has `arg` of `None`; nothing can be said about it here.
            if keyword.arg is not None and keyword.arg not in accepted
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
    name it uses is one the installed library actually has, and every keyword it passes to one is
    one that name's signature accepts.
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
        problems.extend(f"{operation.script}: {problem}" for problem in unaccepted_keywords(source, library=library))
    return problems
