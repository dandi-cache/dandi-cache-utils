"""Streaming remote NWB files and walking their internal structure.

Two things were reimplemented in every cache that touches an NWB file, each in a slightly
different and slightly wrong way: deciding whether an asset is HDF5 or Zarr, and opening it
without downloading it. Both are here.

Nothing is ever downloaded. HDF5 assets are read through `remfile`, which serves `h5py` ranged
reads over HTTPS; Zarr assets are read through their consolidated metadata, so the whole hierarchy
arrives in a single request and the walk never touches the network again.

The `valid-nwb-file-to-*` family all answer structural questions about the same files -- how many
groups, how many datasets, how deep, what the chunking looks like -- so `walk_structure` collects
them in one pass rather than each cache writing its own traversal.
"""

import dataclasses
import pathlib
import typing

from . import s3

#: What this module offers on `<TAB>`. Everything here is defined below; the module's own
#: imports are deliberately left out, which is what `__dir__` at the foot of the file enforces.
__all__ = [
    "HDF5",
    "Structure",
    "ZARR",
    "count_datasets",
    "count_groups",
    "detect_layout",
    "electrical_series_paths",
    "inspect_nwbfile",
    "inspector_config",
    "is_nwb_path",
    "is_zarr_path",
    "open_hdf5",
    "open_nwbfile",
    "open_zarr",
    "walk_structure",
]

HDF5 = "hdf5"
ZARR = "zarr"


def is_nwb_path(path: str, /) -> bool:
    """Whether a Dandiset path names an NWB asset: `.nwb` (HDF5) or `.nwb.zarr` (Zarr)."""
    suffixes = pathlib.PurePosixPath(path).suffixes
    return suffixes[-2:] == [".nwb", ".zarr"] or suffixes[-1:] == [".nwb"]


def is_zarr_path(path: str, /) -> bool:
    """Whether a Dandiset path names a Zarr-backed asset."""
    return ".zarr" in pathlib.PurePosixPath(path).suffixes


def detect_layout(content_id: str, /, *, client=None) -> str:
    """Whether a content ID is stored as an HDF5 blob or a Zarr store.

    The content ID alone does not say which layout it uses, so the blob key is probed first and the
    asset is treated as Zarr when no such blob exists.
    """
    client = client if client is not None else s3.anonymous_client(max_pool_connections=4)
    return HDF5 if s3.object_exists(client, s3.blob_key(content_id)) else ZARR


@dataclasses.dataclass
class Structure:
    """The structural summary of one NWB file, collected in a single traversal."""

    layout: str
    number_of_groups: int = 0
    number_of_datasets: int = 0
    max_depth: int = 0
    total_size_bytes: int | None = None
    #: Depth of each leaf dataset, which is what the tree-shape indices are computed from.
    leaf_depths: list[int] = dataclasses.field(default_factory=list)

    def describe(self) -> str:
        """A short one-line description for the per-item progress log."""
        size = f", {self.total_size_bytes / 1e6:.1f} MB" if self.total_size_bytes else ""
        return f"{self.layout.upper()}{size}, {self.number_of_groups} groups, {self.number_of_datasets} datasets"


def open_hdf5(url: str, /):
    """Open a remote HDF5 file for streaming reads; returns `(h5py.File, remfile.File)`.

    Both are returned because the caller usually wants the file's byte length for its log line, and
    `remfile` is the only one that knows it.
    """
    import h5py
    import remfile

    remote_file = remfile.File(url=url)
    return h5py.File(name=remote_file, mode="r"), remote_file


def open_zarr(content_id: str, /):
    """Open a remote Zarr store's root group through its consolidated metadata.

    DANDI writes `.zmetadata` for every Zarr asset, so the whole hierarchy loads in one request.
    The rare asset without it falls back to the plain store.
    """
    import s3fs
    import zarr

    filesystem = s3fs.S3FileSystem(anon=True)
    store = s3fs.S3Map(root=f"{s3.BUCKET}/{s3.zarr_key(content_id)}", s3=filesystem, check=False)
    try:
        return zarr.open_consolidated(store=store, mode="r")
    except KeyError:
        return zarr.open_group(store=store, mode="r")


def open_nwbfile(url: str, path: str, /):
    """Read a remote asset as a `pynwb.NWBFile`, choosing the reader from the asset's path.

    Returns `(nwbfile, io)`; keep `io` alive for as long as the file is read, since the data is
    streamed lazily rather than held in memory.
    """
    import pynwb

    if is_zarr_path(path):
        import hdmf_zarr

        io = hdmf_zarr.NWBZarrIO(url, mode="r")
        return io.read(), io

    h5py_file, _remote_file = open_hdf5(url)
    io = pynwb.NWBHDF5IO(file=h5py_file, mode="r", load_namespaces=True)
    return io.read(), io


def walk_structure(content_id: str, /, *, client=None, layout: str | None = None) -> Structure:
    """Stream one asset by content ID and summarize its internal structure in one pass."""
    layout = layout if layout is not None else detect_layout(content_id, client=client)
    if layout == HDF5:
        return _walk_hdf5(content_id)
    return _walk_zarr(content_id)


def _walk_hdf5(content_id: str, /) -> Structure:
    import h5py

    h5py_file, remote_file = open_hdf5(s3.blob_url(content_id))
    # The root `/` is itself a group, and `visititems` does not visit it.
    structure = Structure(layout=HDF5, number_of_groups=1, total_size_bytes=remote_file.length)

    def _visit(name: str, item: object) -> None:
        depth = name.count("/") + 1
        structure.max_depth = max(structure.max_depth, depth)
        if isinstance(item, h5py.Group):
            structure.number_of_groups += 1
        else:
            structure.number_of_datasets += 1
            structure.leaf_depths.append(depth)

    with h5py_file:
        h5py_file.visititems(_visit)
    return structure


def _walk_zarr(content_id: str, /) -> Structure:
    root_group = open_zarr(content_id)
    structure = Structure(layout=ZARR, number_of_groups=1)

    def _walk(group, depth: int) -> None:
        structure.max_depth = max(structure.max_depth, depth)
        for _name, _array in group.arrays():
            structure.number_of_datasets += 1
            structure.leaf_depths.append(depth + 1)
        for _name, subgroup in group.groups():
            structure.number_of_groups += 1
            _walk(subgroup, depth + 1)

    _walk(root_group, 0)
    return structure


def count_groups(content_id: str, /, *, client=None) -> tuple[int, str]:
    """Count the groups in one asset, root included; returns the count and a log description."""
    structure = walk_structure(content_id, client=client)
    return structure.number_of_groups, structure.describe()


def count_datasets(content_id: str, /, *, client=None) -> tuple[int, str]:
    """Count the datasets (HDF5) or arrays (Zarr) in one asset; returns the count and a description."""
    structure = walk_structure(content_id, client=client)
    return structure.number_of_datasets, structure.describe()


def inspector_config(keyword: str = "dandi", /):
    """Load an NWB Inspector configuration once, to be reused across a whole batch."""
    import nwbinspector

    return nwbinspector.load_config(keyword)


def inspect_nwbfile(url: str, path: str, /, *, config=None, importance_threshold: str = "CRITICAL") -> list[str]:
    """Run the NWB Inspector over a remote asset with the DANDI configuration.

    Returns the formatted messages at or above the threshold; an empty list means the file is valid
    at that threshold. Pass `config` from `inspector_config()` to avoid reloading it per file.
    """
    import nwbinspector

    nwbfile, _io = open_nwbfile(url, path)
    messages = nwbinspector.inspect_nwbfile_object(
        nwbfile_object=nwbfile,
        config=config if config is not None else inspector_config(),
        importance_threshold=getattr(nwbinspector.Importance, importance_threshold),
    )
    return [str(message) for message in messages]


def electrical_series_paths(url: str, /, *, prefix: str = "acquisition/") -> list[str]:
    """The SpikeInterface-visible ElectricalSeries paths in a remote NWB file, filtered by prefix."""
    import spikeinterface.extractors

    paths: typing.Iterable[str] = (
        spikeinterface.extractors.NwbRecordingExtractor.fetch_available_electrical_series_paths(
            file_path=url, stream_mode="remfile"
        )
    )
    return [series_path for series_path in paths if series_path.startswith(prefix)]


def __dir__() -> list[str]:
    return list(__all__)
