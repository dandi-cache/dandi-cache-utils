"""Streaming an upstream cache's Zarr store over HTTPS, against a local stand-in for GitHub's raw files.

The stand-in serves a directory laid out as `<owner>/<repository>/<commit>/<path>`, compresses what it
sends when asked, as GitHub does, and can be told to drop requests, which is what a few hundred
requests over a real network do now and then.
"""

import gzip
import http.server
import json
import pathlib
import subprocess
import threading

import pytest

import dandi_cache_utils as dandi_cache
from dandi_cache_utils import _remote, _zarr

COMMIT = "0123456789abcdef0123456789abcdef01234567"


class _Handler(http.server.SimpleHTTPRequestHandler):
    drops: list = []  # Paths to answer with a 503 the first time each is asked for.
    requested: list = []

    def log_message(self, *arguments):
        pass

    def do_GET(self):  # noqa: N802  (http.server's spelling)
        type(self).requested.append(self.path)
        if self.path in type(self).drops:
            type(self).drops.remove(self.path)
            self.send_error(503)
            return
        path = pathlib.Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        if "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A raw-file server rooted at `tmp_path / "raw"`, with no waiting between retries."""
    root = tmp_path / "raw"
    root.mkdir()
    _Handler.drops, _Handler.requested = [], []

    def handler(*arguments, **keywords):
        return _Handler(*arguments, directory=str(root), **keywords)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(_remote, "RAW_URL_TEMPLATE", f"http://127.0.0.1:{httpd.server_port}/{{owner}}/{{repository}}/{{commit}}/{{path}}")
    monkeypatch.setattr(_remote, "RETRY_DELAYS", (0, 0, 0, 0))
    yield root
    httpd.shutdown()


def publish(root: pathlib.Path, lines: list[str], /, *, chunk_bytes: int = 1_000, compressed: bool = False):
    """Lay a store out as `dandi-cache/up-stream` serves it at `COMMIT`, and return its local path."""
    store = root / "dandi-cache" / "up-stream" / COMMIT / "derivatives" / "up_stream.zarr"
    _zarr.write_fresh(store, lines, chunk_bytes=chunk_bytes, compressed=compressed)
    return store


def remote() -> _remote.RemoteStore:
    return _remote.RemoteStore.at_commit("https://github.com/dandi-cache/up-stream.git", COMMIT, "derivatives/up_stream.zarr")


def lines_of(count: int, /) -> list[str]:
    return [json.dumps({f"id-{index:04d}": {"n": index}}) for index in range(count)]


@pytest.mark.ai_generated
def test_every_line_is_read_in_order_across_chunks(server):
    store = publish(server, lines_of(200))

    assert len(list((store / "stream" / "c").iterdir())) > 3
    assert [raw.decode() for raw in remote().iter_raw_lines()] == lines_of(200)


@pytest.mark.ai_generated
def test_a_chunk_compressed_in_transit_and_with_the_gzip_codec_reads_the_same(server):
    """`dist` stores gzip each chunk, and GitHub may gzip it again on the way."""
    publish(server, lines_of(50), compressed=True)

    assert [raw.decode() for raw in remote().iter_raw_lines()] == lines_of(50)


@pytest.mark.ai_generated
def test_the_latest_line_of_each_key_wins_in_one_pass(server):
    store = publish(server, lines_of(100))
    _zarr.append_lines(store, [json.dumps({"id-0003": {"n": "new"}}), json.dumps({"id-0099": {"n": "newer"}})], chunk_bytes=1_000)

    latest = [json.loads(line) for line in remote().iter_latest_lines()]
    merged = {key: value for record in latest for key, value in record.items()}

    assert len(latest) == 100
    assert merged["id-0003"] == {"n": "new"}
    assert merged["id-0099"] == {"n": "newer"}
    # One pass: each chunk is fetched once.
    chunk_requests = [path for path in _Handler.requested if "/stream/c/" in path]
    assert len(chunk_requests) == len(set(chunk_requests))


@pytest.mark.ai_generated
def test_a_dropped_request_is_retried(server):
    publish(server, lines_of(30))
    _Handler.drops = [f"/dandi-cache/up-stream/{COMMIT}/derivatives/up_stream.zarr/stream/c/0"]

    assert len(list(remote().iter_raw_lines())) == 30


@pytest.mark.ai_generated
def test_a_store_that_is_not_there_says_so(server):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        list(remote().iter_raw_lines())


@pytest.mark.ai_generated
def test_only_a_github_repository_can_be_streamed():
    with pytest.raises(ValueError, match="not a GitHub repository"):
        _remote.github_repository("https://gitlab.com/someone/something.git")


def git(directory: pathlib.Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def streamed_dataset(tmp_path):
    """A cache whose one input is streamed, with the input's commit pinned as the pipeline pins it."""
    base = tmp_path / "dataset"
    base.mkdir()
    git(base, "init", "-q")
    git(base, "update-index", "--add", "--cacheinfo", f"160000,{COMMIT},sourcedata/up-stream")
    git(base, "commit", "-q", "-m", "pin")
    config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache"}, "inputs": [{"name": "up-stream", "stream": True}]}, directory=base
    )
    return dandi_cache.CacheDataset(config=config, base_directory=base)


@pytest.mark.ai_generated
def test_a_streamed_input_is_read_at_the_pinned_commit_with_nothing_on_disk(server, streamed_dataset):
    store = publish(server, lines_of(120))
    _zarr.append_lines(store, [json.dumps({"id-0007": {"n": "new"}})], chunk_bytes=1_000)

    items = dict(streamed_dataset.iter_input())

    assert len(items) == 120
    assert items["id-0007"] == {"n": "new"}
    assert streamed_dataset.read_input() == items
    assert not (streamed_dataset.base_directory / "sourcedata" / "up-stream").exists()


@pytest.mark.ai_generated
def test_a_streamed_input_without_a_pinned_commit_says_how_to_pin_one(tmp_path):
    base = tmp_path / "dataset"
    base.mkdir()
    git(base, "init", "-q")
    config = dandi_cache.parse_config(
        {"cache": {"name": "my-cache"}, "inputs": [{"name": "up-stream", "stream": True}]}, directory=base
    )
    dataset = dandi_cache.CacheDataset(config=config, base_directory=base)

    with pytest.raises(FileNotFoundError, match="update-index"):
        dict(dataset.iter_input())
