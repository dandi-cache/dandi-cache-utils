"""Run logging: the CI job log, the persistent per-run log, and the accumulating error logs.

Every cache logs the same three things, so all three live here:

- a timestamped log file per run under `logs/`, which the pipeline declares as an output of the
  recorded run so each completed update's log is committed next to the results it produced;
- one line per processed item with what it was, how long it took, and the peak memory so far, so
  a run killed mid-batch shows in the CI job log exactly where it got to;
- long-lived error logs that accumulate across runs, kept under GitHub's per-blob size limit.
"""

import datetime
import logging
import pathlib
import resource
import sys
import time
import traceback
import types
import typing

__all__ = [
    "ErrorLog",
    "LOG_DIRECTORY_NAME",
    "MAX_LOG_FILE_SIZE_BYTES",
    "StagedErrorLog",
    "configure_logging",
    "logger",
    "peak_memory_mib",
]

LOG_DIRECTORY_NAME = "logs"

# The `derivatives` dataset is persistent: error logs accumulate across every run forever and
# GitHub hard-rejects any single blob over 100 MB. Drop the oldest entries once a log grows past
# this cap so it never approaches that limit.
MAX_LOG_FILE_SIZE_BYTES = 80_000_000

_ENTRY_SEPARATOR = b"\n\n"

logger = logging.getLogger("dandi_cache")


def peak_memory_mib() -> float:
    """Peak resident set size of this process so far, in MiB (Linux reports `ru_maxrss` in KiB)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def configure_logging(log_directory: pathlib.Path, /, *, stem: str = "update") -> pathlib.Path:
    """Send the log to stdout and to a new timestamped file under `log_directory`; return the file.

    Stdout is the live view in the CI job log and is flushed per record, so a run killed mid-batch
    still shows how far it got. The file is the persistent copy, kept with the results. `stem`
    separates the kinds of run -- `update`, `refresh`, `testing` -- so a testing run's log is never
    mistaken for an update of the real cache.
    """
    log_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    log_file_path = log_directory / f"{stem}_{timestamp}.log"

    formatter = logging.Formatter(fmt="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    formatter.converter = time.gmtime  # UTC timestamps, matching the file name and the CI log.

    logger.setLevel(logging.INFO)
    # Configuring twice in one process (a test, or an entry point that wraps another) would
    # otherwise duplicate every line once per extra call.
    for existing_handler in list(logger.handlers):
        logger.removeHandler(existing_handler)
        existing_handler.close()

    for handler in (_FlushingStreamHandler(stream=sys.stdout), logging.FileHandler(filename=log_file_path)):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return log_file_path


class _FlushingStreamHandler(logging.StreamHandler):
    """A stream handler that flushes every record, so the CI job log stays live during a long batch."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


class ErrorLog:
    """An append-only error log that stays under GitHub's per-blob size limit.

    Entries are separated by a blank line. Once the file grows past `max_size_bytes` the oldest
    entries are dropped and the file is realigned to the start of a whole entry, so a partial
    report is never left behind.
    """

    def __init__(self, file_path: pathlib.Path, /, *, max_size_bytes: int = MAX_LOG_FILE_SIZE_BYTES) -> None:
        self.file_path = file_path
        self.max_size_bytes = max_size_bytes

    def append(self, message: str, /) -> None:
        """Append one error report, truncating the oldest entries if the file has grown too large."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open(mode="a") as file_stream:
            file_stream.write(f"{message}\n\n")

        if self.file_path.stat().st_size <= self.max_size_bytes:
            return

        with self.file_path.open(mode="rb") as file_stream:
            file_stream.seek(-self.max_size_bytes, 2)
            tail = file_stream.read()

        next_entry_offset = tail.find(_ENTRY_SEPARATOR)
        if next_entry_offset != -1:
            tail = tail[next_entry_offset + len(_ENTRY_SEPARATOR) :]

        with self.file_path.open(mode="wb") as file_stream:
            file_stream.write(tail)


class StagedErrorLog:
    """Routes each failure to the error log for the processing stage it happened in.

    Caches that stream remote files fail in a handful of distinct ways -- the DANDI API is down,
    the file will not open, the analysis itself raised -- and separating those logs is what makes a
    week of failures triageable. Anything raised outside a labelled stage lands in the catch-all.

    Use it as a context manager per item; the stage is set as the work proceeds::

        with staged_errors.item(content_id, dandiset_id=dandiset_id, path=path) as item:
            item.stage = "opening the NWB file"
            ...
    """

    def __init__(
        self,
        log_directory: pathlib.Path,
        /,
        *,
        stages: typing.Mapping[str, str],
        unexpected_file_name: str = "unexpected_errors.txt",
        prefix: str = "",
    ) -> None:
        self.log_directory = log_directory
        self.stage_logs = {stage: ErrorLog(log_directory / f"{prefix}{name}") for stage, name in stages.items()}
        self.unexpected_log = ErrorLog(log_directory / f"{prefix}{unexpected_file_name}")

    def log_for(self, stage: str | None, /) -> ErrorLog:
        """The error log for one stage, falling back to the catch-all for anything unmapped."""
        return self.stage_logs.get(stage, self.unexpected_log) if stage is not None else self.unexpected_log

    def item(self, identifier: str, /, **context: typing.Any) -> "_StagedItem":
        """Open a per-item scope that records any exception under the stage it happened in."""
        return _StagedItem(parent=self, identifier=identifier, context=context)


class _StagedItem:
    """The per-item scope handed out by `StagedErrorLog.item`."""

    def __init__(self, *, parent: StagedErrorLog, identifier: str, context: dict) -> None:
        self._parent = parent
        self.identifier = identifier
        self.context = context
        self.stage: str | None = None
        self.exception: BaseException | None = None

    def __enter__(self) -> "_StagedItem":
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback_object: types.TracebackType | None,
    ) -> bool:
        if exception is None:
            return False
        # A cancelled or killed run is not a per-item failure; let it propagate and stop the batch.
        if isinstance(exception, (KeyboardInterrupt, SystemExit, MemoryError)):
            return False

        self.exception = exception

        context = ", ".join(f"{key} {value}" for key, value in self.context.items())
        stage = self.stage or "processing"
        self._parent.log_for(self.stage).append(
            f"Error while {stage} for `{self.identifier}`"
            f"{f' ({context})' if context else ''}!\n\n"
            f"{type(exception)}: {exception}\n\n"
            f"{''.join(traceback.format_exception(exception_type, exception, traceback_object))}"
        )
        logger.warning("%s: failed while %s (%s: %s)", self.identifier, stage, type(exception).__name__, exception)
        return True

    @property
    def failed(self) -> bool:
        """Whether the scope ended in a recorded failure."""
        return self.exception is not None

    @property
    def error_summary(self) -> str:
        """A one-line `<stage>: <Type>: <message>` summary, for recording alongside the result."""
        if self.exception is None:
            return ""
        return f"{self.stage or 'processing'}: {type(self.exception).__name__}: {self.exception}"
