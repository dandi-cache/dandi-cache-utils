"""The worked conversions are checked, not just written.

`examples/` holds real caches rewritten on this library. They are the documentation of what a cache
repository becomes, so an API change that would break them should fail here rather than in a cache
repository's next scheduled run.
"""

import compileall
import pathlib

import pytest

from dandi_cache_utils import config

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted(path for path in (REPOSITORY_ROOT / "examples").iterdir() if path.is_dir())


@pytest.mark.ai_generated
def test_there_are_worked_examples():
    assert EXAMPLES, "examples/ should hold at least one converted cache."


@pytest.mark.ai_generated
@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_every_example_config_is_valid(example):
    parsed = config.read_config(example / config.CONFIG_FILE_NAME)

    assert parsed.name == example.name
    assert parsed.image == f"ghcr.io/dandi-cache/{example.name}"


@pytest.mark.ai_generated
@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_every_declared_operation_has_a_script(example):
    parsed = config.read_config(example / config.CONFIG_FILE_NAME)

    for operation in parsed.operations.values():
        assert (
            example / operation.script
        ).is_file(), f"{example.name} declares {operation.script} but has no such file"


@pytest.mark.ai_generated
@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_every_example_compiles(example):
    assert compileall.compile_dir(str(example / "code"), quiet=1), f"{example.name} has a syntax error"


@pytest.mark.ai_generated
def test_the_pipeline_and_its_requirements_are_part_of_the_package():
    """They are data the package ships, not a tree beside it that an installation could miss."""
    from dandi_cache_utils import pipeline

    assert pipeline.script_path().is_file()
    assert pipeline.runner_requirements_path().is_file()
    assert pipeline.script_path().parent.name == "pipeline"
