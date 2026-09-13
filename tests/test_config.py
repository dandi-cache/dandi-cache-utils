"""Tests for the declarative cache configuration."""

import pathlib

import pytest

from dandi_cache_utils import config


def write_config(directory: pathlib.Path, text: str) -> pathlib.Path:
    file_path = directory / config.CONFIG_FILE_NAME
    file_path.write_text(text)
    return file_path


def test_defaults_are_derived_from_the_cache_name(tmp_path):
    file_path = write_config(tmp_path, '[cache]\nname = "valid-nwb-file-to-number-of-groups"\n')

    parsed = config.read_config(file_path)

    assert parsed.file_stem == "valid_nwb_file_to_number_of_groups"
    assert parsed.cache_file_name == "valid_nwb_file_to_number_of_groups.jsonl"
    assert parsed.image == "ghcr.io/dandi-cache/valid-nwb-file-to-number-of-groups"
    assert parsed.outputs == ("valid_nwb_file_to_number_of_groups.jsonl",)
    assert parsed.directory == tmp_path.resolve()


def test_an_input_needs_only_its_name(tmp_path):
    file_path = write_config(
        tmp_path,
        '[cache]\nname = "my-cache"\n\n[[inputs]]\nname = "content-id-to-valid-nwb-file"\n',
    )

    only_input = config.read_config(file_path).only_input

    assert only_input.url == "https://github.com/dandi-cache/content-id-to-valid-nwb-file.git"
    assert only_input.path == "sourcedata/content-id-to-valid-nwb-file"
    assert only_input.branch == "derivatives"
    assert only_input.file_name == "content_id_to_valid_nwb_file.jsonl"
    assert only_input.format == "lookup"
    assert str(only_input.relative_file_path) == (
        "sourcedata/content-id-to-valid-nwb-file/derivatives/content_id_to_valid_nwb_file.jsonl"
    )


def test_several_inputs_are_kept_in_order(tmp_path):
    file_path = write_config(
        tmp_path,
        "\n".join(
            [
                "[cache]",
                'name = "qualifying-aind-content-ids"',
                "",
                "[[inputs]]",
                'name = "qualifying-lfp-content-ids"',
                "",
                "[[inputs]]",
                'name = "content-id-to-usage-dandiset-path"',
                "",
                "[[inputs]]",
                'name = "content-id-to-valid-nwb-file"',
                "",
            ]
        ),
    )

    parsed = config.read_config(file_path)

    assert [entry.name for entry in parsed.inputs] == [
        "qualifying-lfp-content-ids",
        "content-id-to-usage-dandiset-path",
        "content-id-to-valid-nwb-file",
    ]
    assert parsed.input("content-id-to-valid-nwb-file").path == "sourcedata/content-id-to-valid-nwb-file"


def test_update_is_always_an_operation(tmp_path):
    file_path = write_config(tmp_path, '[cache]\nname = "my-cache"\n')

    update = config.read_config(file_path).operation("update")

    assert update.script == "code/update.py"
    assert update.label == "Update"
    assert update.limit is None


def test_extra_operations_are_declared(tmp_path):
    file_path = write_config(
        tmp_path,
        '[cache]\nname = "my-cache"\n\n[operations.update]\nlimit = 500\n\n[operations.refresh]\nlabel = "Refresh"\n',
    )

    parsed = config.read_config(file_path)

    assert parsed.operation("update").limit == 500
    assert parsed.operation("refresh").script == "code/refresh.py"
    assert parsed.operation("refresh").label == "Refresh"


def test_an_unknown_operation_names_the_declared_ones(tmp_path):
    parsed = config.read_config(write_config(tmp_path, '[cache]\nname = "my-cache"\n'))

    with pytest.raises(KeyError, match="update"):
        parsed.operation("rebuild")


@pytest.mark.parametrize(
    "text",
    [
        '[cache]\nfile_stem = "no_name"\n',
        '[cache]\nname = "Not_Hyphenated"\n',
        '[cache]\nname = "my-cache"\noutputs = ["results.yaml"]\n',
        '[cache]\nname = "my-cache"\n\n[[inputs]]\nname = "a"\nformat = "yaml"\n',
        '[cache]\nname = "my-cache"\n\n[operations.update]\nlimit = 0\n',
        '[cache]\nname = "my-cache"\n\n[operations.update]\nscript = "../outside.py"\n',
    ],
)
def test_invalid_configurations_are_rejected(tmp_path, text):
    file_path = write_config(tmp_path, text)

    with pytest.raises((ValueError, TypeError)):
        config.read_config(file_path)


def test_two_inputs_cannot_share_a_path(tmp_path):
    file_path = write_config(
        tmp_path,
        '[cache]\nname = "my-cache"\n\n[[inputs]]\nname = "a"\npath = "sourcedata/x"\n\n'
        '[[inputs]]\nname = "b"\npath = "sourcedata/x"\n',
    )

    with pytest.raises(ValueError, match="same `path`"):
        config.read_config(file_path)


def test_shell_rendering_round_trips_through_bash(tmp_path):
    parsed = config.read_config(
        write_config(
            tmp_path,
            '[cache]\nname = "my-cache"\noutputs = ["a.jsonl", "b.jsonl"]\n\n'
            '[[inputs]]\nname = "up-one"\n\n[[inputs]]\nname = "up-two"\nbranch = "min"\n',
        )
    )

    rendered = config.as_shell(parsed)

    assert "CACHE_NAME=my-cache" in rendered
    assert "CACHE_OUTPUTS=(a.jsonl b.jsonl)" in rendered
    assert "INPUT_BRANCHES=(derivatives min)" in rendered
    assert "OPERATION_SCRIPT=code/update.py" in rendered


def test_shell_rendering_quotes_awkward_values(tmp_path):
    parsed = config.read_config(
        write_config(tmp_path, '[cache]\nname = "my-cache"\n\n[[inputs]]\nname = "up"\npath = "source data/up"\n')
    )

    assert "INPUT_PATHS=('source data/up')" in config.as_shell(parsed)


def test_dataset_description_is_generated(tmp_path):
    parsed = config.read_config(
        write_config(
            tmp_path,
            '[cache]\nname = "my-cache"\n\n[description]\ntitle = "My Cache"\nauthors = ["Cody Baker"]\n'
            'keywords = ["DANDI", "NWB"]\n',
        )
    )

    assert parsed.description["Name"] == "My Cache"
    assert parsed.description["DatasetType"] == "study"
    assert parsed.description["License"] == "CC-BY-4.0"
    assert parsed.description["Authors"] == ["Cody Baker"]
    assert parsed.description["Keywords"] == ["DANDI", "NWB"]
    assert parsed.description["ReferencesAndLinks"] == ["https://github.com/dandi-cache/my-cache"]


def test_the_config_is_found_from_a_subdirectory(tmp_path):
    write_config(tmp_path, '[cache]\nname = "my-cache"\n')
    nested = tmp_path / "code" / "deeper"
    nested.mkdir(parents=True)

    assert config.load_config(nested).name == "my-cache"


def test_the_environment_variable_wins(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    file_path = write_config(elsewhere, '[cache]\nname = "pointed-at"\n')
    write_config(tmp_path, '[cache]\nname = "nearby"\n')
    monkeypatch.setenv(config.CONFIG_PATH_VARIABLE, str(file_path))

    assert config.load_config(tmp_path).name == "pointed-at"
