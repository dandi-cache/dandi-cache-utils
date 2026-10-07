"""The `dandi-cache` command, which the pipeline calls once it has an environment."""

import json
import pathlib

import click.testing
import pytest

import dandi_cache_utils

EXAMPLE = "docs/examples/valid-nwb-file-to-number-of-groups/cache.toml"


def invoke(*arguments: str) -> click.testing.Result:
    """Run the command line the way a shell would, and hand back the result."""
    return click.testing.CliRunner().invoke(dandi_cache_utils.dandi_cache_cli, list(arguments))


@pytest.mark.ai_generated
def test_config_shell_renders_the_operation_bash_will_run():
    result = invoke("config", "shell", EXAMPLE, "--operation", "update")

    assert result.exit_code == 0
    assert "OPERATION_SCRIPT=code/update.py" in result.output


@pytest.mark.ai_generated
def test_config_shell_renders_a_second_entry_point(tmp_path):
    """A cache may declare more than `update`, and the pipeline runs whichever one it is given."""
    config_file = tmp_path / "cache.toml"
    config_file.write_text('[cache]\nname = "my-cache"\n\n[operations.refresh]\n')

    result = invoke("config", "shell", str(config_file), "--operation", "refresh")

    assert result.exit_code == 0
    assert "OPERATION_SCRIPT=code/refresh.py" in result.output


@pytest.mark.ai_generated
def test_config_shell_renders_assignments_bash_can_evaluate():
    result = invoke("config", "shell", EXAMPLE)

    assert result.exit_code == 0
    assert "CACHE_NAME=valid-nwb-file-to-number-of-groups" in result.output
    assert "CACHE_OUTPUTS=(" in result.output


@pytest.mark.ai_generated
def test_config_show_is_for_a_human():
    result = invoke("config", "show", EXAMPLE)

    assert result.exit_code == 0
    assert "valid-nwb-file-to-number-of-groups" in result.output


@pytest.mark.ai_generated
def test_dataset_description_writes_the_declared_metadata(tmp_path):
    output = tmp_path / "nested" / "dataset_description.json"

    result = invoke("dataset-description", EXAMPLE, "--output", str(output))

    assert result.exit_code == 0
    description = json.loads(output.read_text())
    assert description["Name"] == "valid-nwb-file-to-number-of-groups"
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


LIMIT = dandi_cache_utils.GITHUB_FILE_LIMIT_BYTES


def _sized(path: pathlib.Path, size: int, /) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.truncate(size)


@pytest.mark.ai_generated
def test_check_sizes_passes_quietly_when_every_file_is_small(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    (tmp_path / "derivatives").mkdir()
    (tmp_path / "derivatives" / "cache.jsonl").write_text("{}\n")

    result = invoke("check-sizes", str(tmp_path))

    assert result.exit_code == 0
    assert "No file under" in result.output


@pytest.mark.ai_generated
def test_check_sizes_warns_near_the_limit_and_reports_it_as_a_step_output(tmp_path, monkeypatch):
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    _sized(tmp_path / "dataset" / "derivatives" / "cache.jsonl", int(0.9 * LIMIT))

    result = invoke("check-sizes", str(tmp_path / "dataset"))

    assert result.exit_code == 0
    assert "::warning" in result.output
    assert "Declare `cache.jsonl` in `split`" in result.output
    assert output.read_text().startswith("size-warnings<<SIZE_WARNINGS_END\nderivatives/cache.jsonl is ")
    assert "derivatives/cache.jsonl" in (tmp_path / "summary.md").read_text()


@pytest.mark.ai_generated
def test_check_sizes_fails_past_the_limit_and_skips_nested_repositories(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    _sized(tmp_path / "logs" / "errors.txt", LIMIT + 1)
    # An input subdataset is its own repository, and a push of this one does not upload it.
    (tmp_path / "sourcedata" / "upstream").mkdir(parents=True)
    (tmp_path / "sourcedata" / "upstream" / ".git").write_text("gitdir: elsewhere\n")
    _sized(tmp_path / "sourcedata" / "upstream" / "derivatives" / "big.jsonl", LIMIT + 1)

    result = invoke("check-sizes", str(tmp_path))

    assert result.exit_code == 1
    assert "::error" in result.output
    assert "logs/errors.txt" in result.output
    assert "big.jsonl" not in result.output


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


def _cache(tmp_path, /, *, title: str = "DANDI Cache: My Cache") -> pathlib.Path:
    """A minimal cache directory, the shape `--check` runs against inside a cache image."""
    config_file = tmp_path / "cache.toml"
    config_file.write_text(f'[cache]\nname = "my-cache"\n\n[description]\ntitle = "{title}"\n')
    return config_file


@pytest.mark.ai_generated
def test_the_declared_copy_records_no_generating_version():
    """A version baked into a committed file would go stale on the library's next release."""
    published = json.loads(invoke("dataset-description", EXAMPLE).output)
    declared = json.loads(invoke("dataset-description", EXAMPLE, "--declared").output)

    assert "Version" in published["GeneratedBy"][0]
    assert "Version" not in declared["GeneratedBy"][0]
    assert {key: value for key, value in published.items() if key != "GeneratedBy"} == {
        key: value for key, value in declared.items() if key != "GeneratedBy"
    }


@pytest.mark.ai_generated
def test_check_passes_on_a_copy_the_configuration_declares(tmp_path):
    config_file = _cache(tmp_path)
    invoke(
        "dataset-description", str(config_file), "--declared", "--output", str(tmp_path / "dataset_description.json")
    )

    result = invoke("dataset-description", str(config_file), "--check")

    assert result.exit_code == 0


@pytest.mark.ai_generated
def test_check_fails_on_a_copy_that_has_drifted_and_says_how(tmp_path):
    """The whole point: a committed file and a `cache.toml` that disagree is what this catches."""
    config_file = _cache(tmp_path)
    invoke(
        "dataset-description", str(config_file), "--declared", "--output", str(tmp_path / "dataset_description.json")
    )
    _cache(tmp_path, title="DANDI Cache: Renamed")

    result = invoke("dataset-description", str(config_file), "--check")

    assert result.exit_code == 1
    assert '-    "Name": "DANDI Cache: My Cache"' in result.output
    assert '+    "Name": "DANDI Cache: Renamed"' in result.output
    assert "--declared --output dataset_description.json" in result.output


@pytest.mark.ai_generated
def test_check_passes_where_no_copy_is_committed(tmp_path):
    """Adopting the file is what makes it checked, so a cache without one is not yet failing."""
    result = invoke("dataset-description", str(_cache(tmp_path)), "--check")

    assert result.exit_code == 0
    assert "does not exist" in result.output


@pytest.mark.ai_generated
def test_check_rejects_a_published_copy_committed_by_mistake(tmp_path):
    """The published rendering carries a version, so committing one is exactly the drift to catch."""
    config_file = _cache(tmp_path)
    invoke("dataset-description", str(config_file), "--output", str(tmp_path / "dataset_description.json"))

    result = invoke("dataset-description", str(config_file), "--check")

    assert result.exit_code == 1
    assert '"Version"' in result.output
