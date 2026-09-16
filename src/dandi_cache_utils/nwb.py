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
    "LINKS_FOLLOWED",
    "LINKS_SKIPPED",
    "Structure",
    "ZARR",
    "count_datasets",
    "count_groups",
    "detect_layout",
    "electrical_series_paths",
    "inspect_nwbfile",
    "inspect_nwbfile_object",
    "inspector_config",
    "is_nwb_path",
    "is_zarr_path",
    "open_hdf5",
    "open_nwbfile",
    "open_zarr",
    "walk_hdf5_group",
    "walk_structure",
    "walk_zarr_group",
]

HDF5 = "hdf5"
ZARR = "zarr"

#: How the structural walk treats an HDF5 link that is not a hard link.
#:
#: These describe two different trees, and a file that contains a soft link has two different
#: answers to every structural question. Half of the archive's NWB files contain one, so this is
#: not a corner: `/acquisition/<series>/imaging_plane` is routinely a soft link to the imaging
#: plane's real home under `/general/optophysiology`.
#:
#: `LINKS_SKIPPED` walks the hard-link object tree: a soft or external link is not a child, and an
#: object reachable by two hard links is counted once, at the first path that reaches it. This is
#: exactly what `h5py.Group.visititems` does, and therefore what the caches that predate this
#: module have already published.
#:
#: `LINKS_FOLLOWED` walks the hierarchy as it is named: every entry of a group is a child of it,
#: whatever kind of link put it there. A group that has already been walked is not walked again --
#: that is what stops a link cycle -- but it is still a child of the node that named it.
#:
#: Neither is the right one. A cache picks the one its published values were computed with.
LINKS_SKIPPED = "skipped"
LINKS_FOLLOWED = "followed"


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
    #: Depth of every leaf, where a leaf is a dataset **or a group with no children**.
    #:
    #: The empty group matters: it is a leaf of the tree as much as a dataset is, and every cache
    #: that computes a tree-shape index has always counted it as one. An earlier version of this
    #: field counted datasets only, which would have quietly changed every published index for a
    #: file containing an empty group.
    leaf_depths: list[int] = dataclasses.field(default_factory=list)
    #: Number of children of each internal node, a node being internal when it has any children.
    #: Leaves contribute nothing, so this is shorter than the number of nodes.
    out_degrees: list[int] = dataclasses.field(default_factory=list)
    #: Total cophenetic index (Mir, Rossello & Rotger, 2013): the sum, over unordered leaf pairs,
    #: of the depth of their lowest common ancestor.
    #:
    #: Accumulated during the walk rather than computed after it, because it is a property of the
    #: tree's shape and the walk is the only place the shape exists. `leaf_depths` cannot recover
    #: it: two very differently shaped trees can have identical leaf depths.
    total_cophenetic_index: int = 0
    #: Which link policy produced this, for an HDF5 file. `None` for Zarr, which has no links.
    links: str | None = None

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


def walk_structure(
    content_id: str, /, *, client=None, layout: str | None = None, links: str = LINKS_SKIPPED
) -> Structure:
    """Stream one asset by content ID and summarize its internal structure in one pass.

    `links` picks which tree an HDF5 file is taken to be; see `LINKS_SKIPPED` and `LINKS_FOLLOWED`.
    It is ignored for Zarr, which has no links.
    """
    layout = layout if layout is not None else detect_layout(content_id, client=client)
    if layout == ZARR:
        return walk_zarr_group(open_zarr(content_id))

    h5py_file, remote_file = open_hdf5(s3.blob_url(content_id))
    with h5py_file:
        structure = walk_hdf5_group(h5py_file, links=links)
    structure.total_size_bytes = remote_file.length
    return structure


def walk_hdf5_group(root_group, /, *, links: str = LINKS_SKIPPED) -> Structure:
    """Summarize an already-open HDF5 group, `root_group` counting as the root of the tree.

    Recursive rather than `visititems`, which cannot replace it: a flat visit gives every node's
    path but not its children, so it can say how many datasets a file holds and not how they are
    arranged. The out-degrees and the cophenetic index both need the arrangement, and an empty
    group is never visited as a group in its own right, so a flat visit cannot even see every leaf.

    Recursing means guarding against a link that resolves back to a group already walked, which
    `visititems` did for us. The guard is the group's on-disk address, so a cycle is walked once
    rather than forever.
    """
    import h5py

    followed = links == LINKS_FOLLOWED
    structure = Structure(layout=HDF5, links=links)
    #: Object header addresses, which is what makes two paths to one object recognizable as one.
    visited: set[int] = set()

    def _address(item) -> int:
        return h5py.h5o.get_info(item.id).addr

    def _children(group):
        """The group's children under this link policy, in the order `visititems` would see them."""
        for name in group.keys():
            if followed or isinstance(group.get(name, getlink=True), h5py.HardLink):
                yield group[name]

    def _walk(group, depth: int) -> int:
        """Accumulate into `structure`; return the number of leaves at or below `group`."""
        address = _address(group)
        if address in visited:
            # Reached a second time. Its leaves were already counted through the path that first
            # reached it, so it contributes none here rather than being counted twice.
            return 0
        visited.add(address)
        structure.number_of_groups += 1
        structure.max_depth = max(structure.max_depth, depth)

        # `len` counts every link, soft ones included, so "has no children" means the same thing
        # under both policies: a group holding nothing but a soft link is not a leaf, even where
        # that link is not walked.
        if not len(group):
            structure.leaf_depths.append(depth)
            return 1

        children = list(_children(group))
        if children:
            structure.out_degrees.append(len(children))

        leaves_per_child = []
        for child in children:
            if isinstance(child, h5py.Group):
                leaves_per_child.append(_walk(child, depth + 1))
                continue
            if not followed:
                child_address = _address(child)
                if child_address in visited:
                    leaves_per_child.append(0)
                    continue
                visited.add(child_address)
            structure.number_of_datasets += 1
            structure.max_depth = max(structure.max_depth, depth + 1)
            structure.leaf_depths.append(depth + 1)
            leaves_per_child.append(1)

        _accumulate_cophenetic(structure, depth=depth, leaves_per_child=leaves_per_child)
        return sum(leaves_per_child)

    _walk(root_group, 0)
    return structure


def walk_zarr_group(root_group, /) -> Structure:
    """Summarize an already-open Zarr group, the Zarr counterpart of `walk_hdf5_group`.

    No link policy: a Zarr hierarchy is a tree of directories, so there is only one tree to walk.
    """
    structure = Structure(layout=ZARR)

    def _walk(group, depth: int) -> int:
        structure.number_of_groups += 1
        structure.max_depth = max(structure.max_depth, depth)
        arrays = list(group.arrays())
        subgroups = list(group.groups())
        if not arrays and not subgroups:
            structure.leaf_depths.append(depth)
            return 1

        structure.out_degrees.append(len(arrays) + len(subgroups))
        structure.max_depth = max(structure.max_depth, depth + 1)

        leaves_per_child = []
        for _name, _array in arrays:
            structure.number_of_datasets += 1
            structure.leaf_depths.append(depth + 1)
            leaves_per_child.append(1)
        for _name, subgroup in subgroups:
            leaves_per_child.append(_walk(subgroup, depth + 1))

        _accumulate_cophenetic(structure, depth=depth, leaves_per_child=leaves_per_child)
        return sum(leaves_per_child)

    _walk(root_group, 0)
    return structure


def _accumulate_cophenetic(structure: Structure, /, *, depth: int, leaves_per_child: list[int]) -> None:
    """Add one internal node's contribution to the total cophenetic index.

    A leaf pair whose lowest common ancestor is exactly this node has its two leaves under two
    *different* children: a pair under one child meets deeper down, inside that child's subtree.
    Squaring the node's own leaf count over-counts by exactly the same-child pairs, so

        (L^2 - sum over children of L_c^2) / 2

    is the number of unordered leaf pairs first joined here, and each contributes this node's
    depth. Every leaf pair has exactly one lowest common ancestor, so summing over the internal
    nodes counts each pair exactly once.
    """
    total = sum(leaves_per_child)
    pairs_first_joined_here = (total * total - sum(count * count for count in leaves_per_child)) // 2
    structure.total_cophenetic_index += depth * pairs_first_joined_here


def count_groups(content_id: str, /, *, client=None, links: str = LINKS_SKIPPED) -> tuple[int, str]:
    """Count the groups in one asset, root included; returns the count and a log description."""
    structure = walk_structure(content_id, client=client, links=links)
    return structure.number_of_groups, structure.describe()


def count_datasets(content_id: str, /, *, client=None, links: str = LINKS_SKIPPED) -> tuple[int, str]:
    """Count the datasets (HDF5) or arrays (Zarr) in one asset; returns the count and a description."""
    structure = walk_structure(content_id, client=client, links=links)
    return structure.number_of_datasets, structure.describe()


def inspector_config(keyword: str = "dandi", /):
    """Load an NWB Inspector configuration once, to be reused across a whole batch."""
    import nwbinspector

    return nwbinspector.load_config(keyword)


def inspect_nwbfile(url: str, path: str, /, *, config=None, importance_threshold: str = "CRITICAL") -> list[str]:
    """Open a remote asset and run the NWB Inspector over it with the DANDI configuration.

    Returns the formatted messages at or above the threshold; an empty list means the file is valid
    at that threshold. Pass `config` from `inspector_config()` to avoid reloading it per file.
    """
    nwbfile, _io = open_nwbfile(url, path)
    return inspect_nwbfile_object(nwbfile, config=config, importance_threshold=importance_threshold)


def inspect_nwbfile_object(nwbfile, /, *, config=None, importance_threshold: str = "CRITICAL") -> list[str]:
    """Run the NWB Inspector over an already-open file, returning the formatted messages.

    Separate from `inspect_nwbfile` because opening a remote file and inspecting it fail for
    unrelated reasons, and a cache that keeps an error log per failure mode has to be able to tell
    which of the two it was.
    """
    import nwbinspector

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
