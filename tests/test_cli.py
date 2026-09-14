"""The `dandi-cache` command, which the pipeline calls once it has an environment."""

import json

import pytest
from click.testing import CliRunner

from dandi_cache_utils import _cli

EXAMPLE = "examples/content-id-to-valid-nwb-file/cache.toml"


@pytest.fixture
def runner():
    return CliRunner()


def test_config_shell_renders_assignments_bash_can_evaluate(runner):
    result = runner.invoke(_cli.dandi_cache_cli, ["config", "shell", EXAMPLE])

    assert result.exit_code == 0
    assert "CACHE_NAME=content-id-to-valid-nwb-file" in result.output
    assert "CACHE_OUTPUTS=(" in result.output


def test_config_shell_selects_the_operation(runner):
    result = runner.invoke(_cli.dandi_cache_cli, ["config", "shell", EXAMPLE, "--operation", "refresh"])

    assert result.exit_code == 0
    assert "OPERATION_SCRIPT=code/refresh.py" in result.output


def test_config_show_is_for_a_human(runner):
    result = runner.invoke(_cli.dandi_cache_cli, ["config", "show", EXAMPLE])

    assert result.exit_code == 0
    assert "content-id-to-valid-nwb-file" in result.output


def test_dataset_description_writes_the_declared_metadata(runner, tmp_path):
    output = tmp_path / "nested" / "dataset_description.json"

    result = runner.invoke(_cli.dandi_cache_cli, ["dataset-description", EXAMPLE, "--output", str(output)])

    assert result.exit_code == 0
    description = json.loads(output.read_text())
    assert description["Name"] == "content-id-to-valid-nwb-file"
    assert description["DatasetType"] == "study"


def test_compress_reports_when_there_is_nothing_to_do(runner, tmp_path):
    result = runner.invoke(_cli.dandi_cache_cli, ["compress", "--base-directory", str(tmp_path)])

    assert result.exit_code == 0
    assert "No derivatives/*.jsonl files found" in result.output


def test_compress_writes_one_archive_per_derivative(runner, tmp_path):
    derivatives = tmp_path / "derivatives"
    derivatives.mkdir()
    (derivatives / "example.jsonl").write_text('{"a": 1}\n')

    result = runner.invoke(_cli.dandi_cache_cli, ["compress", "--base-directory", str(tmp_path)])

    assert result.exit_code == 0
    assert (derivatives / "example.jsonl.gz").is_file()


def test_a_missing_config_is_rejected_by_the_command_line(runner):
    result = runner.invoke(_cli.dandi_cache_cli, ["config", "show", "no/such/cache.toml"])

    assert result.exit_code != 0
