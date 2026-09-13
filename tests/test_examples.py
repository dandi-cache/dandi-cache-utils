"""The worked conversions are checked, not just written.

`examples/` holds real caches rewritten on this library. They are the documentation of what a cache
repository becomes, so an API change that would break them should fail here rather than in a cache
repository's next scheduled run.
"""

import compileall
import pathlib
import tomllib

import pytest

from dandi_cache_utils import config

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted(path for path in (REPOSITORY_ROOT / "examples").iterdir() if path.is_dir())
TEMPLATE = REPOSITORY_ROOT / "templates"


def test_there_are_worked_examples():
    assert EXAMPLES, "examples/ should hold at least one converted cache."


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_every_example_config_is_valid(example):
    parsed = config.read_config(example / config.CONFIG_FILE_NAME)

    assert parsed.name == example.name
    assert parsed.image == f"ghcr.io/dandi-cache/{example.name}"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_every_declared_operation_has_a_script(example):
    parsed = config.read_config(example / config.CONFIG_FILE_NAME)

    for operation in parsed.operations.values():
        assert (
            example / operation.script
        ).is_file(), f"{example.name} declares {operation.script} but has no such file"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_every_example_compiles(example):
    assert compileall.compile_dir(str(example / "code"), quiet=1), f"{example.name} has a syntax error"


def test_the_template_config_is_a_valid_starting_point():
    # The template ships with placeholders in place of the cache name, so it is checked for shape
    # by substituting a plausible name rather than by being read as-is.
    text = (TEMPLATE / config.CONFIG_FILE_NAME).read_text()
    text = text.replace("<cache-name>", "my-cache").replace("<input-cache-name>", "up-stream")

    parsed = config.parse_config(tomllib.loads(text))

    assert parsed.name == "my-cache"
    assert parsed.only_input.name == "up-stream"
    assert parsed.operation("update").script == "code/update.py"


def test_the_template_code_compiles():
    assert compileall.compile_dir(str(TEMPLATE / "code"), quiet=1)
