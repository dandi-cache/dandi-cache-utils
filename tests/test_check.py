"""The check that a cache's own operation script matches the library it will run against.

This exists because of a real gap. A cache's image build verifies the image and the `cache.toml`,
and never the one file the cache itself contributes, so a script calling a function the installed
library does not have passed every check and failed at the next scheduled run, on real data.

The test that matters most is `test_an_import_would_not_catch_this`: it pins *why* the check reads
the syntax tree rather than importing the module, which is the thing a later simplification would
otherwise undo.
"""

import textwrap
import types

import pytest

from dandi_cache_utils import _check, _config

SOURCE = textwrap.dedent("""
    import dandi_cache_utils as dandi_cache
    import numpy

    WORKERS = 16

    def main():
        client = dandi_cache.s3.anonymous_client(max_pool_connections=WORKERS)
        return numpy.median(list(dandi_cache.s3.dandiset_ids(client)))
    """)


@pytest.fixture
def library():
    """A stand-in for the installed library, so a test says what it offers rather than guessing."""
    return types.SimpleNamespace(s3=types.SimpleNamespace(anonymous_client=object(), dandiset_ids=object()))


def test_the_alias_is_read_from_the_import(library):
    """Caches write `import dandi_cache_utils as dandi_cache`, but the alias is theirs to choose."""
    assert _check.referenced_names(SOURCE) == {("s3", "anonymous_client"), ("s3", "dandiset_ids")}


def test_another_libraries_attributes_are_not_ours_to_check(library):
    """`numpy.median` is in the source and is not a claim about this library."""
    assert all(chain[0] != "median" for chain in _check.referenced_names(SOURCE))


def test_nothing_is_unresolved_when_the_library_offers_it(library):
    assert _check.unresolved_names(SOURCE, library=library) == []


def test_a_name_the_library_lacks_is_reported(library):
    """The real scenario: a script written against a release the image does not carry yet."""
    source = SOURCE.replace("dandiset_ids", "dandiset_identifiers")
    assert _check.unresolved_names(source, library=library) == ["s3.dandiset_identifiers"]


def test_an_import_would_not_catch_this(tmp_path):
    """Why this reads the syntax tree instead of importing, pinned so it is not simplified away.

    The name is used inside a function body, so it is resolved when that function runs. Importing
    the module executes its top level and never touches it.
    """
    import importlib.util

    module_path = tmp_path / "operation.py"
    module_path.write_text(
        SOURCE.replace("dandiset_ids", "dandiset_identifiers").replace("import numpy", "numpy = None")
    )
    specification = importlib.util.spec_from_file_location("operation", module_path)
    module = importlib.util.module_from_spec(specification)

    specification.loader.exec_module(module)  # No error: the bad name is never reached.

    assert _check.unresolved_names(module_path.read_text(), library=object()) != []


def write_cache(directory, source, /, *, name="a-cache"):
    (directory / "code").mkdir(exist_ok=True)
    (directory / "code" / "update.py").write_text(source)
    (directory / "cache.toml").write_text(f'[cache]\nname = "{name}"\n')
    return _config.load_config(directory / "cache.toml")


def test_a_sound_script_reports_nothing(tmp_path, library):
    config = write_cache(tmp_path, SOURCE)
    assert _check.check_operations(config, library=library) == []


def test_a_missing_script_is_reported(tmp_path, library):
    config = write_cache(tmp_path, SOURCE)
    (tmp_path / "code" / "update.py").unlink()
    (problem,) = _check.check_operations(config, library=library)
    assert "not found" in problem


def test_a_script_that_does_not_parse_is_reported(tmp_path, library):
    config = write_cache(tmp_path, "def main(:\n    pass\n")
    (problem,) = _check.check_operations(config, library=library)
    assert "does not parse" in problem


def test_a_missing_library_name_is_reported_with_its_script(tmp_path, library):
    config = write_cache(tmp_path, SOURCE.replace("dandiset_ids", "dandiset_identifiers"))
    (problem,) = _check.check_operations(config, library=library)
    assert problem.startswith("code/update.py:")
    assert "dandiset_identifiers" in problem
