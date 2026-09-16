"""The archive-reading helpers, against a stand-in for the bucket.

These exist because four caches had a copy of each of them, and the copies were the kind that
drift quietly: an error-code list that swallows one code too many, a `contentUrl` index read off
by one. A stub client is enough to pin all of it, and pinning it here is what lets a cache delete
its copy.
"""

import json

import pytest

from dandi_cache_utils import s3

#: `botocore.exceptions` rather than `botocore`: the error type lives in the submodule, and
#: importing the package alone does not bring it in.
botocore_exceptions = pytest.importorskip("botocore.exceptions")


class StubClient:
    """The slice of the S3 client these helpers use: a paginator and `get_object`.

    `objects` maps a key to its bytes; a key mapped to an error code raises that code instead,
    which is how an embargoed or deleted manifest is spelled.
    """

    def __init__(self, objects):
        self.objects = objects

    def get_paginator(self, _name):
        return self

    def paginate(self, *, Bucket, Prefix, Delimiter=None):  # noqa: N803  (botocore's spelling)
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        if Delimiter is None:
            yield {"Contents": [{"Key": key} for key in keys]}
            return
        # The listing's directory mode: one entry per distinct first segment below the prefix.
        prefixes = sorted({f"{Prefix}{key[len(Prefix):].split(Delimiter)[0]}{Delimiter}" for key in keys})
        yield {"CommonPrefixes": [{"Prefix": prefix} for prefix in prefixes]}

    def get_object(self, *, Bucket, Key):  # noqa: N803
        value = self.objects[Key]
        if isinstance(value, str):
            raise botocore_exceptions.ClientError({"Error": {"Code": value}}, "GetObject")
        return {"Body": _Body(value)}


class _Body:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload


def manifest(*assets):
    return json.dumps(list(assets)).encode()


@pytest.fixture
def client():
    return StubClient(
        {
            "dandisets/000003/draft/assets.jsonld": manifest({"path": "a.nwb", "contentSize": 10}),
            "dandisets/000003/0.1.0/assets.jsonld": manifest({"path": "a.nwb", "contentSize": 10}),
            "dandisets/000003/draft/dandiset.jsonld": json.dumps({"name": "A study"}).encode(),
            "dandisets/000108/draft/assets.jsonld": manifest(),
            "dandisets/000404/draft/assets.jsonld": "AccessDenied",
        }
    )


def test_every_dandiset_is_listed(client):
    """One entry per Dandiset, not per object under it, and not one per version."""
    assert list(s3.dandiset_ids(client)) == ["000003", "000108", "000404"]


def test_every_version_of_a_manifest_is_listed(client):
    """Not only `draft`: a published version holds assets its draft may since have dropped."""
    assert list(s3.asset_manifest_keys(client)) == [
        "dandisets/000003/0.1.0/assets.jsonld",
        "dandisets/000003/draft/assets.jsonld",
        "dandisets/000108/draft/assets.jsonld",
        "dandisets/000404/draft/assets.jsonld",
    ]


def test_one_dandiset_can_be_listed_alone(client):
    assert list(s3.asset_manifest_keys(client, dandiset_id="000108")) == ["dandisets/000108/draft/assets.jsonld"]


def test_assets_read_by_id_or_by_key(client):
    """A Dandiset ID and a full manifest key reach the same manifest."""
    assert s3.dandiset_assets(client, "000003") == s3.dandiset_assets(client, "dandisets/000003/draft/assets.jsonld")


def test_a_manifest_that_cannot_be_read_is_none_not_an_error(client):
    """An embargoed Dandiset is an expected state of the archive, not a failed run."""
    assert s3.dandiset_assets(client, "000404") is None


def test_an_empty_manifest_is_an_empty_list_not_none(client):
    """A Dandiset with no assets differs from one that could not be read, and both occur."""
    assert s3.dandiset_assets(client, "000108") == []


def test_a_manifest_that_is_not_an_array_raises(client):
    """Counting a mapping's keys as an asset count is the silent failure this prevents."""
    client.objects["dandisets/000999/draft/assets.jsonld"] = json.dumps({"results": []}).encode()
    with pytest.raises(ValueError, match="not a JSON array"):
        s3.dandiset_assets(client, "000999")


def test_an_unexpected_error_still_fails_the_run(client):
    """Only the codes that mean "not readable" are absorbed; a real fault is not."""
    client.objects["dandisets/000500/draft/assets.jsonld"] = "InternalError"
    with pytest.raises(botocore_exceptions.ClientError):
        s3.dandiset_assets(client, "000500")


@pytest.mark.parametrize(
    ("content_urls", "expected"),
    [
        # An HDF5 asset is one blob, so the content ID is the last segment.
        (["https://api.dandiarchive.org/api/assets/x/download/", "https://s3/blobs/abc/def/BLOB-ID"], "BLOB-ID"),
        # A Zarr asset is a directory store, so the ID is the second to last.
        (["https://api.dandiarchive.org/api/assets/x/download/", "https://s3/zarr/ZARR-ID/"], "ZARR-ID"),
    ],
)
def test_the_content_id_comes_from_the_second_url(content_urls, expected):
    """Reading the wrong segment does not fail, it labels every Zarr asset with a filename."""
    assert s3.content_id_from_content_urls(content_urls) == expected
