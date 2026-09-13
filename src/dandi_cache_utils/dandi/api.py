"""Resolving assets through the DANDI REST API.

Three caches independently reimplemented the same three-line resolution, so it lives here once.
The client is always tokenless: a cache publishes its results publicly, so it must only ever see
public Dandisets, and running without a token is what guarantees that.

Prefer `dandi_cache_utils.dandi.s3` when a content ID alone is enough. The API is needed only when
the cache has to go from a Dandiset path to the bytes, or wants asset metadata such as `created`.
"""

import typing


def client():
    """A tokenless DANDI API client, so only public Dandisets are ever reachable."""
    import dandi.dandiapi

    return dandi.dandiapi.DandiAPIClient()


class AssetResolver:
    """Resolves `(dandiset_id, path)` pairs to streamable S3 URLs, caching the lookups.

    A batch usually touches the same Dandiset many times in a row, and re-fetching it per asset is
    the difference between one request and hundreds.
    """

    def __init__(self, api_client=None, /) -> None:
        self.client = api_client if api_client is not None else client()
        self._dandisets: dict = {}

    def _dandiset(self, dandiset_id: str):
        if dandiset_id not in self._dandisets:
            self._dandisets[dandiset_id] = self.client.get_dandiset(dandiset_id=dandiset_id)
        return self._dandisets[dandiset_id]

    def asset(self, dandiset_id: str, path: str, /):
        """The remote asset at `path` within `dandiset_id`."""
        return self._dandiset(dandiset_id).get_asset_by_path(path=path)

    def content_url(self, dandiset_id: str, path: str, /) -> str:
        """The direct S3 URL of an asset, suitable for a streaming reader."""
        return self.asset(dandiset_id, path).get_content_url(follow_redirects=1, strip_query=True)

    def created(self, dandiset_id: str, path: str, /):
        """When an asset was created, used to break ties between several paths for one content ID."""
        return self.asset(dandiset_id, path).created


def split_location(location: typing.Mapping, /) -> tuple[str, str]:
    """Unpack the `{dandiset_id: path}` single-entry mapping the caches use to name an asset."""
    ((dandiset_id, path),) = location.items()
    return dandiset_id, path
