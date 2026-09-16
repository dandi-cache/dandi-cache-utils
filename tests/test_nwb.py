"""The structural walk, checked against the algorithms it replaces.

Every `valid-nwb-file-to-*` cache used to carry its own traversal, and they did not agree. Two of
them walked the hierarchy as named, following soft links and guarding cycles by object address;
the rest called `h5py.Group.visititems`, which traverses hard links only and visits each object
once. On a file with a soft link those are two different trees with two different answers, and
half of the archive's NWB files have one.

So the walk cannot have a single behaviour, and these tests are not "does it look right": they run
each cache's original algorithm, verbatim, over a file built to contain every construct the two
definitions disagree about, and require the walk to reproduce it exactly. Anything less would
silently rewrite tens of thousands of already-published values.

Skipped where the NWB stack is absent -- the `:latest` image has no `h5py`, and running the suite
there is the point of that image's test step.
"""

import math

import pytest

from dandi_cache_utils import nwb

h5py = pytest.importorskip("h5py")
numpy = pytest.importorskip("numpy")
zarr = pytest.importorskip("zarr")


# --- The algorithms the walk has to reproduce, transcribed from the caches ----------------------


def reference_leaf_depths(h5py_file):
    """`valid-nwb-file-to-sackin-index`: leaf depths, where a leaf is a dataset or an empty group."""
    leaf_depths: list[int] = []

    def _visit(name, obj):
        depth = name.count("/") + 1
        if isinstance(obj, h5py.Dataset) or (isinstance(obj, h5py.Group) and len(obj) == 0):
            leaf_depths.append(depth)

    h5py_file.visititems(_visit)
    return leaf_depths


def reference_counts(h5py_file):
    """`valid-nwb-file-to-number-of-groups` and `-number-of-datasets`: the two counts, root included."""
    counts = {"groups": 1, "datasets": 0}

    def _visit(_name, obj):
        counts["groups" if isinstance(obj, h5py.Group) else "datasets"] += 1

    h5py_file.visititems(_visit)
    return counts


def reference_out_degrees(root_group):
    """`valid-nwb-file-to-out-degrees`: the out-degree of every internal node."""
    out_degrees: list[int] = []
    visited: set[int] = set()

    def _walk(group):
        address = h5py.h5o.get_info(group.id).addr
        if address in visited:
            return
        visited.add(address)
        child_names = list(group.keys())
        if not child_names:
            return
        out_degrees.append(len(child_names))
        for child_name in child_names:
            child = group[child_name]
            if isinstance(child, h5py.Group):
                _walk(child)

    _walk(root_group)
    return out_degrees


def reference_cophenetic_index(root_group):
    """`valid-nwb-file-to-cophenetic-index`: the total cophenetic index of the hierarchy."""
    visited: set[int] = set()
    phi = 0

    def _walk(obj, depth):
        nonlocal phi
        if isinstance(obj, h5py.Dataset):
            return 1
        address = h5py.h5o.get_info(obj.id).addr
        if address in visited:
            return 0
        visited.add(address)
        child_names = list(obj.keys())
        if not child_names:
            return 1
        child_leaf_counts = [_walk(obj[name], depth + 1) for name in child_names]
        total = sum(child_leaf_counts)
        phi += depth * ((total * total - sum(count * count for count in child_leaf_counts)) // 2)
        return total

    _walk(root_group, 0)
    return phi


def reference_zarr_leaf_depths(root_group):
    """`valid-nwb-file-to-sackin-index`, Zarr side: arrays and childless groups are the leaves."""
    leaf_depths: list[int] = []

    def _walk(group, depth):
        arrays = list(group.arrays())
        subgroups = list(group.groups())
        if not arrays and not subgroups:
            leaf_depths.append(depth)
            return
        leaf_depths.extend(depth + 1 for _ in arrays)
        for _name, subgroup in subgroups:
            _walk(subgroup, depth + 1)

    _walk(root_group, 0)
    return leaf_depths


def reference_zarr_counts(root_group):
    """`valid-nwb-file-to-number-of-datasets`, Zarr side, plus the group count beside it."""
    counts = {"groups": 1, "datasets": 0}

    def _walk(group):
        counts["datasets"] += len(list(group.arrays()))
        for _name, subgroup in group.groups():
            counts["groups"] += 1
            _walk(subgroup)

    _walk(root_group)
    return counts


def normalized_sackin_index(leaf_depths):
    """`valid-nwb-file-to-sackin-index`'s published value, which is what actually has to hold still."""
    n = len(leaf_depths)
    if n <= 1:
        return 0.0
    sackin_max = n * (n + 1) / 2 - 1
    sackin_min = n * math.ceil(math.log2(n))
    if sackin_max <= sackin_min:
        return 0.0
    return (sum(leaf_depths) - sackin_min) / (sackin_max - sackin_min)


# --- The files the two definitions disagree about -----------------------------------------------


@pytest.fixture
def awkward_hdf5_file(tmp_path):
    """A hierarchy holding every construct the two tree definitions disagree about.

    The shape is deliberately that of a real NWB file: `acquisition/series/imaging_plane` is a soft
    link to the plane's real home under `general`, which is where the archive's own files put it
    and where the two definitions first part company.
    """
    path = tmp_path / "awkward.h5"
    with h5py.File(path, mode="w") as file:
        general = file.create_group("general")
        plane = general.create_group("imaging_plane")
        plane.create_dataset("description", data=1)
        plane.create_dataset("excitation_lambda", data=2)
        general.create_group("devices").create_group("microscope")  # An empty group, so a leaf.

        acquisition = file.create_group("acquisition")
        series = acquisition.create_group("series")
        series.create_dataset("data", data=[1, 2, 3])
        series["imaging_plane"] = h5py.SoftLink("/general/imaging_plane")
        series["starting_time"] = plane["description"]  # A second hard link to one dataset.

        file.create_group("empty")
        file.create_group("only_a_soft_link")["up"] = h5py.SoftLink("/")  # A cycle.
        file["alias"] = general  # A second hard link to one group.

    with h5py.File(path, mode="r") as file:
        yield file


@pytest.fixture
def awkward_zarr_group(tmp_path):
    """The Zarr counterpart: nesting, arrays and an empty group. Zarr has no links, so no cycles."""
    root = zarr.open_group(store=str(tmp_path / "awkward.zarr"), mode="w")
    root.create_dataset("top", shape=(2,), dtype="i4")
    nested = root.create_group("nested")
    nested.create_dataset("inner", shape=(2,), dtype="i4")
    nested.create_group("deeper").create_dataset("leaf", shape=(2,), dtype="i4")
    root.create_group("empty")
    return root


# --- HDF5 ---------------------------------------------------------------------------------------


def test_skipped_links_reproduce_visititems(awkward_hdf5_file):
    """`LINKS_SKIPPED` is `visititems`, which is what the published values were computed with."""
    structure = nwb.walk_hdf5_group(awkward_hdf5_file, links=nwb.LINKS_SKIPPED)
    counts = reference_counts(awkward_hdf5_file)

    assert sorted(structure.leaf_depths) == sorted(reference_leaf_depths(awkward_hdf5_file))
    assert structure.number_of_groups == counts["groups"]
    assert structure.number_of_datasets == counts["datasets"]


def test_skipped_links_reproduce_the_published_sackin_index(awkward_hdf5_file):
    """The property that actually matters: the number the cache writes does not move."""
    structure = nwb.walk_hdf5_group(awkward_hdf5_file, links=nwb.LINKS_SKIPPED)
    assert normalized_sackin_index(structure.leaf_depths) == normalized_sackin_index(
        reference_leaf_depths(awkward_hdf5_file)
    )


def test_followed_links_reproduce_the_out_degrees(awkward_hdf5_file):
    structure = nwb.walk_hdf5_group(awkward_hdf5_file, links=nwb.LINKS_FOLLOWED)
    assert sorted(structure.out_degrees) == sorted(reference_out_degrees(awkward_hdf5_file))


def test_followed_links_reproduce_the_cophenetic_index(awkward_hdf5_file):
    structure = nwb.walk_hdf5_group(awkward_hdf5_file, links=nwb.LINKS_FOLLOWED)
    assert structure.total_cophenetic_index == reference_cophenetic_index(awkward_hdf5_file)


def test_the_two_policies_actually_disagree(awkward_hdf5_file):
    """Guard against the fixture drifting into something the policies happen to agree on.

    If this ever passes trivially, the tests above stop testing anything: they would be comparing
    two spellings of one algorithm rather than two definitions of the tree.
    """
    skipped = nwb.walk_hdf5_group(awkward_hdf5_file, links=nwb.LINKS_SKIPPED)
    followed = nwb.walk_hdf5_group(awkward_hdf5_file, links=nwb.LINKS_FOLLOWED)
    assert sorted(skipped.leaf_depths) != sorted(followed.leaf_depths)


def test_a_link_cycle_terminates(awkward_hdf5_file):
    """`only_a_soft_link/up` points at the root. Without the address guard this never returns."""
    structure = nwb.walk_hdf5_group(awkward_hdf5_file, links=nwb.LINKS_FOLLOWED)
    assert structure.number_of_groups > 0


def test_an_empty_root_is_a_leaf(tmp_path):
    """The degenerate file: no children at all, which the recursion has to survive."""
    path = tmp_path / "empty.h5"
    with h5py.File(path, mode="w"):
        pass
    with h5py.File(path, mode="r") as file:
        structure = nwb.walk_hdf5_group(file)

    assert structure.number_of_groups == 1
    assert structure.number_of_datasets == 0
    assert structure.leaf_depths == [0]
    assert structure.out_degrees == []
    assert structure.total_cophenetic_index == 0
    # One leaf is no spread of imbalance to measure, which is what the cache publishes for it.
    assert normalized_sackin_index(structure.leaf_depths) == 0.0


def test_an_empty_group_is_a_leaf(awkward_hdf5_file):
    """`general/devices/microscope` has no children, so it is a leaf at its own depth, not a node."""
    structure = nwb.walk_hdf5_group(awkward_hdf5_file, links=nwb.LINKS_SKIPPED)
    assert 3 in structure.leaf_depths


def test_the_policy_is_recorded(awkward_hdf5_file):
    """A `Structure` says which tree it describes, since the same file has two."""
    assert nwb.walk_hdf5_group(awkward_hdf5_file, links=nwb.LINKS_FOLLOWED).links == nwb.LINKS_FOLLOWED
    assert nwb.walk_hdf5_group(awkward_hdf5_file).links == nwb.LINKS_SKIPPED


# --- Zarr ---------------------------------------------------------------------------------------


def test_the_zarr_walk_reproduces_the_caches(awkward_zarr_group):
    structure = nwb.walk_zarr_group(awkward_zarr_group)
    counts = reference_zarr_counts(awkward_zarr_group)

    assert sorted(structure.leaf_depths) == sorted(reference_zarr_leaf_depths(awkward_zarr_group))
    assert structure.number_of_groups == counts["groups"]
    assert structure.number_of_datasets == counts["datasets"]
    assert structure.layout == nwb.ZARR
    # Zarr has no links, so there is only one tree and no policy to record.
    assert structure.links is None


def test_an_empty_zarr_root_is_a_leaf(tmp_path):
    structure = nwb.walk_zarr_group(zarr.open_group(store=str(tmp_path / "empty.zarr"), mode="w"))

    assert structure.number_of_groups == 1
    assert structure.leaf_depths == [0]
    assert structure.total_cophenetic_index == 0


# --- The shape indices, on trees whose answers are known by hand --------------------------------


def test_the_cophenetic_index_of_a_known_tree(tmp_path):
    """A balanced binary tree of four leaves: Phi = 4 pairs joined at depth 1, 2 at depth 0.

    Worked by hand rather than by the implementation: the four leaves give six unordered pairs,
    two of which meet at depth 1 (inside their own subtree) and four at the root, depth 0.
    """
    path = tmp_path / "balanced.h5"
    with h5py.File(path, mode="w") as file:
        for branch in ("left", "right"):
            group = file.create_group(branch)
            group.create_dataset("a", data=1)
            group.create_dataset("b", data=2)

    with h5py.File(path, mode="r") as file:
        structure = nwb.walk_hdf5_group(file, links=nwb.LINKS_FOLLOWED)

    assert structure.total_cophenetic_index == 2 * 1 + 4 * 0
    assert sorted(structure.out_degrees) == [2, 2, 2]
    assert sorted(structure.leaf_depths) == [2, 2, 2, 2]
    assert structure.max_depth == 2


def test_the_cophenetic_index_of_a_caterpillar(tmp_path):
    """The other extreme: every internal node has one leaf and one subtree.

    Leaves at depths 1, 2, 3, 3. Pairs meet at the root (depth 0) for the first leaf against the
    rest, at depth 1 for the second against the last two, and at depth 2 for the last two: 1 + 2.
    """
    path = tmp_path / "caterpillar.h5"
    with h5py.File(path, mode="w") as file:
        file.create_dataset("leaf", data=1)
        middle = file.create_group("rest")
        middle.create_dataset("leaf", data=2)
        deepest = middle.create_group("rest")
        deepest.create_dataset("a", data=3)
        deepest.create_dataset("b", data=4)

    with h5py.File(path, mode="r") as file:
        structure = nwb.walk_hdf5_group(file, links=nwb.LINKS_FOLLOWED)

    assert sorted(structure.leaf_depths) == [1, 2, 3, 3]
    assert structure.total_cophenetic_index == 1 * 2 + 2 * 1
