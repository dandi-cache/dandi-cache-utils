"""The orchestration script, shipped as part of the package rather than beside it.

`update_pipeline.sh` is data, not an executable this project installs onto anyone's PATH, so it
lives inside the package and travels with it: a wheel, an editable checkout and the copy vendored
into the container image all carry it at the same place, and `script_path()` finds it in each
without anyone knowing the layout.

`dandi-cache pipeline` runs it; `dandi-cache pipeline --path` prints where it is.
"""

import importlib.resources
import pathlib

__all__ = ["RUNNER_REQUIREMENTS_NAME", "SCRIPT_NAME", "runner_requirements_path", "script_path"]

SCRIPT_NAME = "update_pipeline.sh"

#: What the pipeline installs into the runner's own environment: datalad and the container
#: extension. Pinned here so the orchestration environment ships with the orchestration.
RUNNER_REQUIREMENTS_NAME = "runner-requirements.txt"


def _resource(name: str, /) -> pathlib.Path:
    with importlib.resources.as_file(importlib.resources.files(__name__) / name) as path:
        return path


def script_path() -> pathlib.Path:
    """Where the orchestration script is, in this installation."""
    return _resource(SCRIPT_NAME)


def runner_requirements_path() -> pathlib.Path:
    """Where the runner's pinned requirements are, in this installation."""
    return _resource(RUNNER_REQUIREMENTS_NAME)


def __dir__() -> list[str]:
    return list(__all__)
