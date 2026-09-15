"""The orchestration script, shipped as part of the package rather than beside it.

`update_pipeline.sh` is data, not an executable this project installs onto anyone's PATH, so it
lives inside the package and travels with it: a wheel, an editable checkout and the copy vendored
into the container image all carry it at the same place, and `SCRIPT_PATH` finds it in each
without anyone knowing the layout.

`dandi-cache pipeline` runs it; `dandi-cache pipeline --path` prints where it is.
"""

import pathlib

__all__ = ["RUNNER_REQUIREMENTS_PATH", "SCRIPT_PATH"]

#: These are read from disk, not as package data: CI extracts this directory out of the image and
#: bash runs the script from the copy on the filesystem, so a package that was not a real
#: directory could never serve them anyway.
_DIRECTORY = pathlib.Path(__file__).parent

SCRIPT_PATH = _DIRECTORY / "update_pipeline.sh"

#: What the pipeline installs into the runner's own environment: datalad and the container
#: extension. Pinned here so the orchestration environment ships with the orchestration.
RUNNER_REQUIREMENTS_PATH = _DIRECTORY / "runner-requirements.txt"


def __dir__() -> list[str]:
    return list(__all__)
