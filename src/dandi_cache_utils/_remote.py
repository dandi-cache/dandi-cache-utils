"""Reading an upstream cache's Zarr store over HTTPS, one chunk at a time, without cloning it.

An input declared with `stream = true` is never checked out. The pipeline records the commit of the
upstream's `derivatives` branch as the input subdataset's gitlink, which is the same record a cloned
input gets, and this module reads the store at exactly that commit from GitHub's raw file service.
Only the chunk being read is held, in memory: nothing is written to disk, so an input of many
gigabytes costs a runner neither the clone nor the space. GitHub compresses each file in transit,
and a chunk of JSON Lines compresses well, so an 80 MB chunk crosses the network as a few megabytes.

The reader is the standard library alone, like the rest of what the pipeline imports: each chunk is
JSON Lines text, gzip-compressed or not, so no Zarr library is needed to read one.
"""

import collections
import concurrent.futures
import gzip
import json
import pathlib
import re
import subprocess
import time
import typing
import urllib.error
import urllib.request

from . import _zarr

#: Where GitHub serves one file of a repository at one commit.
RAW_URL_TEMPLATE = "https://raw.githubusercontent.com/{owner}/{repository}/{commit}/{path}"

#: The repository URLs a streamed input can name: GitHub's, which is what serves the raw files.
GITHUB_URL = re.compile(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repository>[^/]+?)(?:\.git)?/?$")

#: Seconds to wait before each retry of a request that failed. A connection the far end drops is
#: ordinary over a few hundred requests, and it is never a reason to fail the run.
RETRY_DELAYS = (2, 4, 8, 16)

#: How many chunks are fetched ahead of the one being read: enough to keep the connection busy,
#: few enough that memory stays at a handful of chunks.
PREFETCH = 2

TIMEOUT_SECONDS = 120


def github_repository(url: str, /) -> tuple[str, str]:
    """The `(owner, repository)` a GitHub repository URL names."""
    match = GITHUB_URL.match(url)
    if match is None:
        raise ValueError(f"{url!r} is not a GitHub repository URL, so its files cannot be streamed.")
    return match["owner"], match["repository"]


def pinned_commit(base_directory: pathlib.Path, input_path: str, /) -> str:
    """The upstream commit the dataset at `base_directory` records for the input at `input_path`.

    That is the input subdataset's gitlink, which the pipeline sets to the tip of the upstream's
    branch before the run. Reading it here, rather than asking the upstream where its branch is now,
    is what keeps the result tied to the exact upstream commit it was computed from.
    """
    result = subprocess.run(
        ["git", "-C", str(base_directory), "rev-parse", "--verify", "--quiet", f"HEAD:{input_path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise FileNotFoundError(
            f"{base_directory} records no commit for the streamed input {input_path}. The pipeline pins one "
            "before each run; outside it, record one with `git update-index --add --cacheinfo "
            f"160000,<commit>,{input_path}` and commit it."
        )
    return commit


class RemoteStore:
    """One upstream Zarr store at one commit, read over HTTPS."""

    def __init__(self, base_url: str, /) -> None:
        self.base_url = base_url.rstrip("/")

    @classmethod
    def at_commit(cls, url: str, commit: str, path: str, /) -> "RemoteStore":
        """The store at `path` in the repository at `url`, as it was at `commit`."""
        owner, repository = github_repository(url)
        return cls(RAW_URL_TEMPLATE.format(owner=owner, repository=repository, commit=commit, path=path))

    def fetch(self, path: str, /, *, missing_ok: bool = False) -> bytes | None:
        """One file of the store, decoded, retrying a failed request before giving up."""
        url = f"{self.base_url}/{path}"
        for delay in (*RETRY_DELAYS, None):
            try:
                return _get(url)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    if missing_ok:
                        return None
                    raise FileNotFoundError(f"{url} does not exist.") from error
                # Throttling and server errors pass; anything else is a request that cannot succeed.
                if delay is None or (error.code < 500 and error.code != 429):
                    raise
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                if delay is None:
                    raise
            time.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def chunk_count(self) -> int:
        metadata = json.loads(self.fetch(f"{_zarr.ARRAY_NAME}/zarr.json"))
        length = metadata["shape"][0]
        chunk_bytes = metadata["chunk_grid"]["configuration"]["chunk_shape"][0]
        return -(-length // chunk_bytes)

    def iter_chunks(self, indices: typing.Sequence[int], /) -> typing.Iterator[bytes]:
        """The decoded chunks at `indices`, in that order, fetching a few ahead of the one yielded.

        A chunk that does not exist holds only padding, as a missing chunk file does locally, and is
        skipped.
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=PREFETCH) as pool:
            pending: collections.deque = collections.deque()
            remaining = iter(indices)
            for index in remaining:
                pending.append(pool.submit(self.fetch, f"{_zarr.ARRAY_NAME}/c/{index}", missing_ok=True))
                if len(pending) >= PREFETCH:
                    break
            while pending:
                data = pending.popleft().result()
                for index in remaining:
                    pending.append(pool.submit(self.fetch, f"{_zarr.ARRAY_NAME}/c/{index}", missing_ok=True))
                    break
                if data is not None:
                    yield _zarr.decode_chunk(data)

    def iter_raw_lines(self) -> typing.Iterator[bytes]:
        """Every non-blank line, in order, as bytes."""
        for chunk in self.iter_chunks(range(self.chunk_count())):
            yield from _zarr.chunk_lines(chunk)

    def iter_latest_lines(self) -> typing.Iterator[str]:
        """The current line of each key of a lookup store, newest first, in one pass.

        A later line for a key replaces an earlier one. Reading the chunks from last to first, and
        each chunk's lines from last to first, makes the first line seen for a key its current one,
        so the store is read once rather than twice, and what is held is the set of keys already seen.
        """
        seen: set = set()
        for chunk in self.iter_chunks(range(self.chunk_count() - 1, -1, -1)):
            for raw in reversed(_zarr.chunk_lines(chunk)):
                key = _zarr.raw_line_key(raw)
                if key not in seen:
                    seen.add(key)
                    yield raw.decode("utf-8")


def _get(url: str, /) -> bytes:
    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return body
