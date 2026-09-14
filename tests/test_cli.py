"""The `dandi-cache` command, which the pipeline calls once it has an environment."""

import json
import pathlib

import click.testing
import pytest

import dandi_cache_utils

EXAMPLE = "examples/content-id-to-valid-nwb-file/cache.toml"


def invoke(*arguments: str) -> click.testing.Result:
    """Run the command line the way a shell would, and hand back the result."""
    return click.testing.CliRunner().invoke(dandi_cache_utils.dandi_cache_cli, list(arguments))


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("operation", "expected_script"),
    [("update", "code/update.py"), ("refresh", "code/refresh.py")],
)
def test_config_shell_renders_the_operation_bash_will_run(operation, expected_script):
    result = invoke("config", "shell", EXAMPLE, "--operation", operation)

    assert result.exit_code == 0
    assert f"OPERATION_SCRIPT={expected_script}" in result.output


@pytest.mark.ai_generated
def test_config_shell_renders_assignments_bash_can_evaluate():
    result = invoke("config", "shell", EXAMPLE)

    assert result.exit_code == 0
    assert "CACHE_NAME=content-id-to-valid-nwb-file" in result.output
    assert "CACHE_OUTPUTS=(" in result.output


@pytest.mark.ai_generated
def test_config_show_is_for_a_human():
    result = invoke("config", "show", EXAMPLE)

    assert result.exit_code == 0
    assert "content-id-to-valid-nwb-file" in result.output


@pytest.mark.ai_generated
def test_dataset_description_writes_the_declared_metadata(tmp_path):
    output = tmp_path / "nested" / "dataset_description.json"

    result = invoke("dataset-description", EXAMPLE, "--output", str(output))

    assert result.exit_code == 0
    description = json.loads(output.read_text())
    assert description["Name"] == "content-id-to-valid-nwb-file"
    assert description["DatasetType"] == "study"


@pytest.mark.ai_generated
def test_compress_reports_when_there_is_nothing_to_do(tmp_path):
    result = invoke("compress", "--base-directory", str(tmp_path))

    assert result.exit_code == 0
    assert "No derivatives/*.jsonl files found" in result.output


@pytest.mark.ai_generated
def test_compress_writes_one_archive_per_derivative(tmp_path):
    derivatives = tmp_path / "derivatives"
    derivatives.mkdir()
    (derivatives / "example.jsonl").write_text('{"a": 1}\n')

    result = invoke("compress", "--base-directory", str(tmp_path))

    assert result.exit_code == 0
    assert (derivatives / "example.jsonl.gz").is_file() is True


@pytest.mark.ai_generated
def test_a_missing_config_is_rejected_by_the_command_line():
    result = invoke("config", "show", "no/such/cache.toml")

    assert result.exit_code != 0


@pytest.mark.ai_generated
def test_the_pipeline_script_ships_with_the_package():
    result = invoke("pipeline", "--path")

    assert result.exit_code == 0
    script = pathlib.Path(result.output.strip())
    assert script.is_file() is True
    assert script.read_text().startswith("#!/usr/bin/env bash") is True
