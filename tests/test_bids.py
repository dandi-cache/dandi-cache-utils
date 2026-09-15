"""The published `dataset_description.json` is checked against the BIDS schema, not against a hunch.

Every cache publishes one of these on its `derivatives` and `dist` branches, rendered from its
`cache.toml`, so a mistake here is a mistake in five published datasets at once. `bidsschematools`
is the BIDS maintainers' own machine-readable spec, which is what makes this a real check rather
than a restatement of what the renderer already does.
"""

import bidsschematools.schema
import pytest

import dandi_cache_utils as dandi_cache

SCHEMA = bidsschematools.schema.load_schema()
METADATA = SCHEMA["objects"]["metadata"]
DATASET_DESCRIPTION_RULES = SCHEMA["rules"]["dataset_metadata"]["dataset_description"]["fields"]

#: Deliberately not the real release: the version published is whatever the caller passes, and
#: pinning it here would mean editing this file on every bump without checking anything more.
VERSION = "9.9.9"

#: A cache of each shape: several inputs and a full description, and the bare minimum.
CONFIGURATIONS = {
    "fully-declared": {
        "cache": {"name": "qualifying-aind-content-ids"},
        "inputs": [{"name": "qualifying-lfp-content-ids"}, {"name": "content-id-to-valid-nwb-file"}],
        "description": {
            "title": "DANDI Cache: Qualifying AIND Content IDs",
            "authors": ["Cody Baker"],
            "keywords": ["DANDI", "ephys"],
        },
    },
    "bare": {"cache": {"name": "my-cache"}},
}


def _level_of(field: str, /) -> str:
    rule = DATASET_DESCRIPTION_RULES[field]
    return rule if isinstance(rule, str) else rule["level"]


def _describe(raw: dict, /) -> dict:
    return dandi_cache.dataset_description(dandi_cache.parse_config(raw), version=VERSION)


@pytest.mark.ai_generated
@pytest.mark.parametrize("raw", CONFIGURATIONS.values(), ids=CONFIGURATIONS)
def test_every_required_field_is_present(raw):
    required = {field for field in DATASET_DESCRIPTION_RULES if _level_of(field) == "required"}

    assert required <= set(_describe(raw)), f"missing what BIDS requires: {required - set(_describe(raw))}"


@pytest.mark.ai_generated
@pytest.mark.parametrize("raw", CONFIGURATIONS.values(), ids=CONFIGURATIONS)
def test_no_field_is_unknown_to_the_schema(raw):
    """A key BIDS has never heard of is a typo, and would be silently published to five datasets."""
    assert set(_describe(raw)) <= set(DATASET_DESCRIPTION_RULES)


@pytest.mark.ai_generated
@pytest.mark.parametrize("raw", CONFIGURATIONS.values(), ids=CONFIGURATIONS)
def test_every_field_has_the_type_the_schema_declares(raw):
    types = {"string": str, "array": list, "object": dict, "boolean": bool}

    for field, value in _describe(raw).items():
        expected = METADATA[field].get("type")
        assert isinstance(value, types[expected]), f"{field} should be {expected}"
        if expected == "array" and METADATA[field].get("items", {}).get("type") == "string":
            assert all(isinstance(entry, str) for entry in value), f"{field} should hold strings"


@pytest.mark.ai_generated
@pytest.mark.parametrize("raw", CONFIGURATIONS.values(), ids=CONFIGURATIONS)
def test_no_cache_claims_a_newer_spec_than_this_check_reads(raw):
    """Conformance to a release this test has never seen would be a claim nothing here checked.

    The bound only ever rises, so upgrading `bidsschematools` cannot fail this; declaring a version
    ahead of the installed spec can, which is the mistake worth catching.
    """
    declared = tuple(int(part) for part in _describe(raw)["BIDSVersion"].split("."))
    spec = tuple(int(part) for part in SCHEMA["bids_version"].split("."))

    assert declared <= spec, f"declared BIDS {declared} over a schema that describes {spec}"


@pytest.mark.ai_generated
def test_the_dataset_type_is_one_the_schema_allows():
    """`study` is one of `raw`, `derivative` and `study`, and is the only one a cache is."""
    assert _describe(CONFIGURATIONS["bare"])["DatasetType"] in METADATA["DatasetType"]["enum"]


@pytest.mark.ai_generated
def test_generated_by_names_the_library_that_generated_it():
    entry = _describe(CONFIGURATIONS["bare"])["GeneratedBy"][0]
    required = set(METADATA["GeneratedBy"]["items"]["required"])

    assert required <= set(entry)
    assert entry["Name"] == "dandi-cache-utils"
    assert entry["Version"] == VERSION
    assert set(entry["Container"]) <= set(METADATA["GeneratedBy"]["items"]["properties"]["Container"]["properties"])
    assert entry["Container"]["ContainerTag"] == "ghcr.io/dandi-cache/my-cache"


@pytest.mark.ai_generated
def test_generated_by_claims_no_version_when_none_is_known():
    """Valid either way: BIDS requires only `Name`, so an unknown version is omitted, not invented."""
    entry = dandi_cache.dataset_description(dandi_cache.parse_config(CONFIGURATIONS["bare"]))["GeneratedBy"][0]

    assert "Version" not in entry
    assert set(METADATA["GeneratedBy"]["items"]["required"]) <= set(entry)


@pytest.mark.ai_generated
def test_the_upstream_caches_are_the_source_datasets():
    """The one place a cache lists its inputs is the same place BIDS wants them declared."""
    described = _describe(CONFIGURATIONS["fully-declared"])

    assert described["SourceDatasets"] == [
        {"URL": "https://github.com/dandi-cache/qualifying-lfp-content-ids.git"},
        {"URL": "https://github.com/dandi-cache/content-id-to-valid-nwb-file.git"},
    ]


@pytest.mark.ai_generated
def test_a_first_in_chain_cache_declares_no_source_datasets():
    """Rather than an empty list: it fetches its own inputs, so there is no upstream dataset."""
    assert "SourceDatasets" not in _describe(CONFIGURATIONS["bare"])
