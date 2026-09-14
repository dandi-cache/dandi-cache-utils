"""Where the version comes from, which is `pyproject.toml` and nowhere else.

An installed copy carries it in its distribution metadata. The copy vendored into the image is
also imported straight from `src/` by the runner's bare `python3`, which installs nothing, so that
one reads `[project] version` out of the `pyproject.toml` shipped beside the sources. Either way
the answer comes from the one place it is written.
"""

import importlib.metadata
import pathlib
import tomllib

__all__ = ["DISTRIBUTION_NAME", "read_version"]

DISTRIBUTION_NAME = "dandi-cache-utils"


def read_version() -> str:
    """Resolve this installation's version."""
    try:
        version = importlib.metadata.version(DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        pyproject_path = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"
        if not pyproject_path.is_file():
            raise
        version = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]["version"]
    return version


def __dir__() -> list[str]:
    return list(__all__)
