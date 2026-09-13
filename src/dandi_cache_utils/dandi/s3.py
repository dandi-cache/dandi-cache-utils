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

BUCKET = "dandiarchive"
REGION = "us-east-2"
PUBLIC_BASE_URL = f"https://{BUCKET}.s3.amazonaws.com"

DANDISET_MANIFEST_KEY = "dandisets/{dandiset_id}/{version}/dandiset.jsonld"
ASSETS_MANIFEST_KEY = "dandisets/{dandiset_id}/{version}/assets.jsonld"

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
