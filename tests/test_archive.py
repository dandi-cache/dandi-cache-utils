"""Which DANDI instance a cache describes, and every archive reader following it.

The readers used to name DANDI's bucket and API outright, so a cache of another instance, such as
EMBER, could not be built from the same code. The instance is now declared once in `cache.toml`,
and these pin that every reader takes it from there unless told otherwise.
"""

import sys
import types

import pytest

import dandi_cache_utils as dandi_cache

DANDI = dandi_cache.ARCHIVES["dandi"]
EMBER = dandi_cache.ARCHIVES["ember-dandi"]


def write_config(directory, text):
    file_path = directory / dandi_cache.CONFIG_FILE_NAME
    file_path.write_text(text)
    return file_path


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """Make the running cache one whose `cache.toml` carries the given `[archive]` table."""

    def configure(archive_table):
        file_path = write_config(tmp_path, f'[cache]\nname = "some-cache"\n\n{archive_table}')
        monkeypatch.setenv(dandi_cache.CONFIG_PATH_VARIABLE, str(file_path))
        dandi_cache.configured_archive.cache_clear()

    yield configure
    dandi_cache.configured_archive.cache_clear()


@pytest.mark.ai_generated
def test_a_cache_without_an_archive_table_describes_dandi(tmp_path):
    parsed = dandi_cache.read_config(write_config(tmp_path, '[cache]\nname = "some-cache"\n'))

    assert parsed.archive == DANDI
    assert (DANDI.bucket, DANDI.region, DANDI.api_url) == (
        "dandiarchive",
        "us-east-2",
        "https://api.dandiarchive.org/api",
    )


@pytest.mark.ai_generated
def test_a_known_instance_is_named_alone(tmp_path):
    parsed = dandi_cache.read_config(
        write_config(tmp_path, '[cache]\nname = "some-cache"\n\n[archive]\nname = "ember-dandi"\n'),
    )

    assert parsed.archive == EMBER
    assert (EMBER.bucket, EMBER.region, EMBER.api_url) == (
        "ember-open-data",
        "us-east-1",
        "https://api-dandi.emberarchive.org/api",
    )


@pytest.mark.ai_generated
def test_another_instance_is_described_in_full(tmp_path):
    text = (
        '[cache]\nname = "some-cache"\n\n[archive]\nname = "elsewhere"\nbucket = "elsewhere-data"\n'
        'region = "eu-west-1"\napi_url = "https://api.elsewhere.org/api/"\n'
    )

    archive = dandi_cache.read_config(write_config(tmp_path, text)).archive

    assert archive == dandi_cache.Archive(
        name="elsewhere", bucket="elsewhere-data", region="eu-west-1", api_url="https://api.elsewhere.org/api"
    )
    assert archive.public_base_url == "https://elsewhere-data.s3.amazonaws.com"


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("table", "message"),
    [
        ('bucket = "b"\n', "requires a `name`"),
        ('name = "nowhere"\n', "not a known instance"),
        ('name = "ember-dandi"\nbucket = "ember-open-data"\n', "Name a known instance alone"),
        ('name = "elsewhere"\nbucket = "b"\nregion = "r"\n', "lacks api_url"),
        ('name = "elsewhere"\nbucket = "b"\nregion = "r"\napi_url = ""\n', "non-empty string"),
        ('name = "ember-dandi"\nendpoint = "x"\n', "unknown keys: endpoint"),
    ],
    ids=["no-name", "unknown-instance", "partial-override", "incomplete", "empty-value", "unknown-key"],
)
def test_an_ambiguous_archive_is_refused(tmp_path, table, message):
    """A bucket of one instance with the API of another would resolve against the wrong Dandisets."""
    file_path = write_config(tmp_path, f'[cache]\nname = "some-cache"\n\n[archive]\n{table}')

    with pytest.raises(ValueError, match=message):
        dandi_cache.read_config(file_path)


@pytest.mark.ai_generated
def test_the_running_cache_declares_the_archive(configured):
    configured('[archive]\nname = "ember-dandi"\n')

    assert dandi_cache.configured_archive() == EMBER


@pytest.mark.ai_generated
def test_outside_any_cache_the_archive_is_dandi(tmp_path, monkeypatch):
    monkeypatch.delenv(dandi_cache.CONFIG_PATH_VARIABLE, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "script.py")])
    dandi_cache.configured_archive.cache_clear()

    try:
        assert dandi_cache.configured_archive() == DANDI
    finally:
        dandi_cache.configured_archive.cache_clear()


class RecordingClient:
    """An S3 client that records which bucket each request named, and finds nothing there."""

    def __init__(self):
        self.buckets = []

    def get_paginator(self, _name):
        return self

    def paginate(self, *, Bucket, Prefix, Delimiter=None):  # noqa: N803  (botocore's spelling)
        self.buckets.append(Bucket)
        yield {}

    def get_object(self, *, Bucket, Key):  # noqa: N803
        self.buckets.append(Bucket)
        return {"Body": types.SimpleNamespace(read=lambda: b"")}

    def head_object(self, *, Bucket, Key):  # noqa: N803
        self.buckets.append(Bucket)


def read_everything(client, **bucket):
    list(dandi_cache.s3.dandiset_ids(client, **bucket))
    list(dandi_cache.s3.asset_manifest_keys(client, **bucket))
    dandi_cache.s3.dandiset_assets(client, "000001", **bucket)
    dandi_cache.s3.dandiset_created(client, "000001", **bucket)
    dandi_cache.s3.object_exists(client, "blobs/abc", **bucket)


@pytest.mark.ai_generated
def test_every_reader_uses_the_configured_bucket(configured):
    pytest.importorskip("botocore.exceptions")
    configured('[archive]\nname = "ember-dandi"\n')
    client = RecordingClient()

    read_everything(client)

    assert set(client.buckets) == {"ember-open-data"}
    assert len(client.buckets) == 5  # noqa: PLR2004


@pytest.mark.ai_generated
def test_a_reader_given_a_bucket_uses_that_one(configured):
    pytest.importorskip("botocore.exceptions")
    configured('[archive]\nname = "ember-dandi"\n')
    client = RecordingClient()

    read_everything(client, bucket="dandiarchive")

    assert set(client.buckets) == {"dandiarchive"}


@pytest.mark.ai_generated
@pytest.mark.parametrize(("archive", "expected"), [("dandi", "dandiarchive"), ("ember-dandi", "ember-open-data")])
def test_blob_urls_point_at_the_configured_bucket(configured, archive, expected):
    configured(f'[archive]\nname = "{archive}"\n')

    assert dandi_cache.s3.blob_url("0123456789") == f"https://{expected}.s3.amazonaws.com/blobs/012/345/0123456789"


@pytest.mark.ai_generated
def test_the_client_is_in_the_configured_region(configured):
    pytest.importorskip("boto3")
    configured('[archive]\nname = "ember-dandi"\n')

    assert dandi_cache.s3.anonymous_client().meta.region_name == "us-east-1"
    assert dandi_cache.s3.anonymous_client(region="eu-west-1").meta.region_name == "eu-west-1"


@pytest.mark.ai_generated
def test_the_dandi_constants_are_unchanged():
    """Caches built before `[archive]` existed still read these, and they still mean DANDI."""
    assert (dandi_cache.s3.BUCKET, dandi_cache.s3.REGION, dandi_cache.s3.PUBLIC_BASE_URL) == (
        "dandiarchive",
        "us-east-2",
        "https://dandiarchive.s3.amazonaws.com",
    )


@pytest.fixture
def fake_dandi(monkeypatch):
    """Stand in for the `dandi` package, recording the API URL each client is built for."""
    built = []

    class DandiAPIClient:
        def __init__(self, api_url=None):
            built.append(api_url)

    dandiapi = types.ModuleType("dandi.dandiapi")
    dandiapi.DandiAPIClient = DandiAPIClient
    package = types.ModuleType("dandi")
    package.dandiapi = dandiapi
    monkeypatch.setitem(sys.modules, "dandi", package)
    monkeypatch.setitem(sys.modules, "dandi.dandiapi", dandiapi)
    return built


@pytest.mark.ai_generated
def test_the_api_client_and_resolver_use_the_configured_api(configured, fake_dandi):
    configured('[archive]\nname = "ember-dandi"\n')

    dandi_cache.api.client()
    dandi_cache.api.AssetResolver()
    dandi_cache.api.client(api_url="https://api.elsewhere.org/api")

    assert fake_dandi == [EMBER.api_url, EMBER.api_url, "https://api.elsewhere.org/api"]


@pytest.mark.ai_generated
def test_zarr_stores_are_opened_in_the_configured_bucket(configured, monkeypatch):
    configured('[archive]\nname = "ember-dandi"\n')
    roots = []

    s3fs = types.ModuleType("s3fs")
    s3fs.S3FileSystem = lambda **kwargs: None
    s3fs.S3Map = lambda *, root, s3, check: roots.append(root)
    zarr = types.ModuleType("zarr")
    zarr.open_consolidated = lambda *, store, mode: "opened"
    monkeypatch.setitem(sys.modules, "s3fs", s3fs)
    monkeypatch.setitem(sys.modules, "zarr", zarr)

    dandi_cache.nwb.open_zarr("some-zarr-id")

    assert roots == ["ember-open-data/zarr/some-zarr-id"]
