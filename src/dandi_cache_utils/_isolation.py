"""Running one item's work in a child process, so a call that hangs can be stopped.

A cache that streams remote files spends most of its time inside `h5py`, reading over HTTP through
`remfile`. A read that stalls, or a file whose chunk index takes far longer to read than its
neighbours, blocks the whole batch, and a signal-based timeout cannot interrupt it safely: the
exception would surface inside an HDF5 callback and leave the library's state undefined. A child
process can always be killed, and the parent's `h5py` is never involved.
"""

import multiprocessing
import pickle
import typing


def _call_in_child(function: typing.Callable, arguments: tuple, connection) -> None:
    """Send back `("value", result)` or `("error", exception)`, whatever the function does."""
    try:
        outcome = ("value", function(*arguments))
    except BaseException as error:  # noqa: BLE001 -- every exception has to cross back to the parent
        outcome = ("error", error)
    try:
        # The round trip, not only the pickling: an exception whose `__init__` takes other
        # arguments than it stores pickles fine and then fails to load in the parent.
        pickle.loads(pickle.dumps(outcome))
    except Exception:  # noqa: BLE001 -- whatever stops the round trip, the parent still needs an answer
        kind, value = outcome
        text = (
            f"{type(value).__name__}: {value}"
            if kind == "error"
            else f"unpicklable result of type {type(value).__name__}"
        )
        outcome = ("error", RuntimeError(text))
    try:
        connection.send(outcome)
    finally:
        connection.close()


def run_isolated(function: typing.Callable, /, *, arguments: tuple = (), timeout_seconds: float) -> typing.Any:
    """Call `function(*arguments)` in a forked child process and return its result.

    Raises `TimeoutError` when the call takes longer than `timeout_seconds`, after killing the child.
    An exception the function raises is raised here as it was, or as a `RuntimeError` naming it
    when it cannot be pickled. A child that dies without answering, killed by the kernel for its
    memory or crashing inside a C extension, raises `ChildProcessError` with its exit code.

    The child is forked, so it inherits everything the parent has imported and `function` need not
    be importable by name. Its result crosses back by pickling, so it should be plain data.
    """
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=_call_in_child, args=(function, arguments, sender), daemon=True)
    child.start()
    sender.close()
    try:
        if not receiver.poll(timeout_seconds):
            raise TimeoutError(f"exceeded {timeout_seconds:g} s")
        try:
            kind, value = receiver.recv()
        except EOFError as error:
            child.join(5)
            raise ChildProcessError(f"the child process exited with code {child.exitcode} without a result") from error
    finally:
        if child.is_alive():
            child.kill()
        child.join(5)
        receiver.close()

    if kind == "error":
        raise value
    return value
