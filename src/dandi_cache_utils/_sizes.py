"""Finding the files that are near, or past, GitHub's limit for one file.

A push carrying a file over 100 MiB is refused outright, and the pipeline only pushes once the
whole run's work is done, so a cache that grows past the limit loses hours of work on every run
until someone notices. This is how it gets noticed first: the pipeline checks what it is about to
push, warns while there is still room, and stops with an actionable message rather than GitHub's.
"""

import dataclasses
import os
import pathlib

from ._config import GITHUB_FILE_LIMIT_BYTES

#: A file past this fraction of the limit is reported. At the growth of the largest caches, about
#: 2 MB a day, it leaves a week or more to act.
WARNING_FRACTION = 0.8

WARNING = "warning"
ERROR = "error"


@dataclasses.dataclass(frozen=True)
class SizeFinding:
    """One file near or past the limit, relative to the directory it was found under."""

    path: pathlib.PurePosixPath
    size_bytes: int
    level: str

    @property
    def fraction(self) -> float:
        """The file's size as a fraction of GitHub's limit."""
        return self.size_bytes / GITHUB_FILE_LIMIT_BYTES

    def describe(self) -> str:
        """What the file is, how close it is, and what to do about it."""
        state = "is past" if self.level == ERROR else "is approaching"
        return (
            f"{self.path} is {self.size_bytes / 1e6:.1f} MB, {self.fraction:.0%} of GitHub's 100 MiB limit for one "
            f"file; it {state} the size at which every push of its branch is refused. {_remedy(self.path)}"
        )


def _remedy(path: pathlib.PurePosixPath, /) -> str:
    if path.name.endswith(".jsonl.gz"):
        return "This is the compressed copy published to `dist`; it needs publishing as several files."
    if path.parts[:1] == ("derivatives",) and path.suffix == ".jsonl":
        return f"Declare `{path.name}` in `split` under `[cache]` in cache.toml to keep it as sixteen files."
    if path.parts[:1] == ("logs",):
        return "Error logs are capped well below this, so a log this large is a bug in the cache."
    return "Keep it smaller, or out of the repository."


def find_large_files(
    directory: pathlib.Path,
    /,
    *,
    warning_fraction: float = WARNING_FRACTION,
) -> list[SizeFinding]:
    """Every file under `directory` past `warning_fraction` of the limit, largest first.

    Git's own directory and any nested repository, such as an input subdataset under `sourcedata/`,
    are skipped: neither is part of what a push of this repository's branch uploads.
    """
    threshold = warning_fraction * GITHUB_FILE_LIMIT_BYTES
    findings = []
    for root, directory_names, file_names in os.walk(directory):
        root_path = pathlib.Path(root)
        directory_names[:] = [
            name
            for name in directory_names
            if name != ".git" and not (root_path / name / ".git").exists()
        ]
        for name in file_names:
            file_path = root_path / name
            if file_path.is_symlink():
                continue
            size = file_path.stat().st_size
            if size > threshold:
                findings.append(
                    SizeFinding(
                        path=pathlib.PurePosixPath(file_path.relative_to(directory).as_posix()),
                        size_bytes=size,
                        level=ERROR if size > GITHUB_FILE_LIMIT_BYTES else WARNING,
                    )
                )
    return sorted(findings, key=lambda finding: -finding.size_bytes)
