"""Reading the public DANDI archive S3 bucket directly.

The bucket is content-addressed, so a cache that already holds a content ID can reach the bytes
without going through the DANDI API at all -- which is both faster and immune to the API's
rate limits and outages.

The lessons encoded here were paid for in production debugging:

- **Listed does not mean readable.** An embargoed Dandiset lists its manifest publicly but denies
  the anonymous read, and an object can be deleted between the listing and the fetch. Both are
  expected upstream states, not run failures, so they come back as `None` rather than raising.
- **Size the connection pool to the worker count.** botocore's default pool of 10 makes every
  surplus thread redo the TCP/TLS handshake on each request.
- **Prefer JSON over YAML.** Parsing large YAML in pure Python is GIL-bound and does not
  parallelize across threads; the archive publishes `assets.jsonld` next to every `assets.yaml`.
"""

import concurrent.futures
import datetime
import json
import typing

#: What this module offers on `<TAB>`. Everything here is defined below; the module's own
#: imports are deliberately left out, which is what `__dir__` at the foot of the file enforces.
__all__ = [
    "ABSENT_ERROR_CODES",
    "ASSETS_MANIFEST_KEY",
    "ASSETS_MANIFEST_SUFFIX",
    "BUCKET",
    "DANDISETS_PREFIX",
    "DANDISET_MANIFEST_KEY",
    "PUBLIC_BASE_URL",
    "REGION",
    "anonymous_client",
    "asset_manifest_keys",
    "blob_key",
    "blob_url",
    "concurrent_map",
    "content_id_from_content_urls",
    "dandiset_assets",
    "dandiset_created",
    "dandiset_ids",
    "dandiset_metadata",
    "get_json",
    "get_object_bytes",
    "object_exists",
    "parse_timestamp",
    "zarr_key",
]

BUCKET = "dandiarchive"
REGION = "us-east-2"
PUBLIC_BASE_URL = f"https://{BUCKET}.s3.amazonaws.com"

DANDISETS_PREFIX = "dandisets/"
DANDISET_MANIFEST_KEY = "dandisets/{dandiset_id}/{version}/dandiset.jsonld"
ASSETS_MANIFEST_KEY = "dandisets/{dandiset_id}/{version}/assets.jsonld"
#: What marks a key as an assets manifest, for listing them without formatting a version in.
ASSETS_MANIFEST_SUFFIX = "/assets.jsonld"

#: Error codes that mean "this object is not readable", rather than "the run has gone wrong".
ABSENT_ERROR_CODES = ("AccessDenied", "NoSuchKey", "404")


def anonymous_client(*, max_pool_connections: int = 16):
    """Build an unsigned S3 client sized for `max_pool_connections` concurrent readers."""
    import boto3
    import botocore
    import botocore.config

    config = botocore.config.Config(
        signature_version=botocore.UNSIGNED,
        max_pool_connections=max_pool_connections,
        retries={"mode": "standard"},
    )
    return boto3.client("s3", region_name=REGION, config=config)


def get_object_bytes(client, key: str, /, *, bucket: str = BUCKET) -> bytes | None:
    """Fetch one object's bytes, or `None` when it is deleted or not publicly readable."""
    import botocore.exceptions

    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except botocore.exceptions.ClientError as error:
        error_code = error.response.get("Error", {}).get("Code", "")
        if error_code in ABSENT_ERROR_CODES:
            return None
        raise
    return response["Body"].read()


def get_json(client, key: str, /, *, bucket: str = BUCKET) -> dict | None:
    """Fetch and parse one JSON object, or `None` when it is absent or unreadable."""
    body = get_object_bytes(client, key, bucket=bucket)
    if body is None or not body.strip():
        return None
    return json.loads(body)


def object_exists(client, key: str, /, *, bucket: str = BUCKET) -> bool:
    """Whether an object exists and is readable anonymously."""
    import botocore.exceptions

    try:
        client.head_object(Bucket=bucket, Key=key)
    except botocore.exceptions.ClientError as error:
        error_code = error.response.get("Error", {}).get("Code", "")
        if error_code in ABSENT_ERROR_CODES:
            return False
        raise
    return True


def blob_key(content_id: str, /) -> str:
    """The bucket key of a content-addressed blob (an HDF5 asset)."""
    return f"blobs/{content_id[:3]}/{content_id[3:6]}/{content_id}"


def blob_url(content_id: str, /) -> str:
    """The public HTTPS URL of a content-addressed blob, for streaming readers."""
    return f"{PUBLIC_BASE_URL}/{blob_key(content_id)}"


def zarr_key(content_id: str, /) -> str:
    """The bucket key prefix of a Zarr asset's store."""
    return f"zarr/{content_id}"


def dandiset_metadata(client, dandiset_id: str, /, *, version: str = "draft") -> dict | None:
    """Read a Dandiset's `dandiset.jsonld` metadata, or `None` if it is not readable."""
    return get_json(client, DANDISET_MANIFEST_KEY.format(dandiset_id=dandiset_id, version=version))


def dandiset_ids(client, /) -> typing.Iterator[str]:
    """Yield every Dandiset ID under `dandisets/`, in lexicographic (S3 listing) order.

    The bucket listing rather than the REST API, for the same reason `dandiset_created` reads S3:
    the API's listing endpoint omits some live Dandisets, so a listing-based pass loses them
    silently. Every Dandiset that has ever been published has a folder here.
    """
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=DANDISETS_PREFIX, Delimiter="/"):
        for entry in page.get("CommonPrefixes", []):
            yield entry["Prefix"].removeprefix(DANDISETS_PREFIX).rstrip("/")


def asset_manifest_keys(client, /, *, dandiset_id: str | None = None) -> typing.Iterator[str]:
    """Yield every `assets.jsonld` key, across every version of every Dandiset.

    Every version, not only `draft`: an asset that a draft has since dropped is still part of the
    published version that holds it, so a cache describing what the archive contains has to see
    them all. Pass `dandiset_id` to walk one Dandiset instead of the whole bucket.
    """
    prefix = DANDISETS_PREFIX if dandiset_id is None else f"{DANDISETS_PREFIX}{dandiset_id}/"
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for entry in page.get("Contents", []):
            if entry["Key"].endswith(ASSETS_MANIFEST_SUFFIX):
                yield entry["Key"]


def dandiset_assets(client, key_or_id: str, /, *, version: str = "draft") -> list | None:
    """Read an assets manifest as its list of asset entries, or `None` when it is not readable.

    Takes either a full manifest key, as `asset_manifest_keys` yields, or a Dandiset ID, in which
    case `version` selects which manifest to read.

    A manifest that is not a JSON array raises rather than being read as one. The count of a
    mapping is its number of keys, which a caller counting assets would otherwise publish as an
    asset count without noticing the archive's layout had changed.
    """
    key = (
        key_or_id
        if key_or_id.endswith(ASSETS_MANIFEST_SUFFIX)
        else ASSETS_MANIFEST_KEY.format(dandiset_id=key_or_id, version=version)
    )
    assets = get_json(client, key)
    if assets is None:
        return None
    if not isinstance(assets, list):
        message = (
            f"The manifest `{key}` is not a JSON array of assets. "
            "The DANDI archive's manifest layout may have changed."
        )
        raise ValueError(message)
    return assets


def content_id_from_content_urls(content_urls: list[str], /) -> str:
    """The content ID an asset entry's `contentUrl` list points at.

    The second URL is the S3 download URL, and its shape depends on the layout: an HDF5 asset is
    a single blob at `.../blobs/<a>/<b>/<content_id>`, so the ID is the last segment, while a Zarr
    asset is a directory store at `.../zarr/<content_id>/`, so the ID is the second to last.
    Reading the wrong segment does not fail, it silently labels every Zarr asset with a filename,
    which is why this is one function rather than a line copied into each cache.
    """
    s3_download_url = content_urls[1]
    return s3_download_url.split("/")[-1] if "blobs" in s3_download_url else s3_download_url.split("/")[-2]


def dandiset_created(client, dandiset_id: str, /, *, version: str = "draft") -> datetime.datetime | None:
    """A Dandiset's creation time from its S3 manifest, or `None` when it cannot be placed in time.

    Read from S3 rather than the REST API on purpose: the API's listing endpoint omits some live
    Dandisets, so a listing-based pass silently loses them.
    """
    metadata = dandiset_metadata(client, dandiset_id, version=version)
    if metadata is None:
        return None

    date_created = metadata.get("dateCreated")
    if date_created is None:
        return None
    return parse_timestamp(date_created)


def parse_timestamp(value: str, /) -> datetime.datetime | None:
    """Parse an archive timestamp into an aware UTC datetime, tolerating a trailing `Z`."""
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=datetime.UTC)


def concurrent_map(
    function: typing.Callable,
    items: typing.Sequence,
    /,
    *,
    max_workers: int = 16,
) -> list:
    """Run `function` over `items` in a thread pool, preserving order.

    Network reads of the archive are latency-bound rather than CPU-bound, so threads help; size the
    client's connection pool to `max_workers` or the surplus threads gain nothing.
    """
    if not items:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(function, items))


def __dir__() -> list[str]:
    return list(__all__)
