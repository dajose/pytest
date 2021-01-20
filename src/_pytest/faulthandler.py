import io
import os
import sys
from typing import Generator
from typing import TextIO

import pytest
from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.nodes import Item
from _pytest.store import StoreKey


fault_handler_stderr_key = StoreKey[TextIO]()


def pytest_addoption(parser: Parser) -> None:
    help = (
        "Dump the traceback of all threads if a test takes "
        "more than TIMEOUT seconds to finish."
    )
    parser.addini("faulthandler_timeout", help, default=0.0)


def pytest_configure(config: Config) -> None:
    import faulthandler

    stderr_fd_copy = os.dup(get_stderr_fileno())
    config._store[fault_handler_stderr_key] = open(stderr_fd_copy, "w")
    faulthandler.enable(file=config._store[fault_handler_stderr_key])


def pytest_unconfigure(config: Config) -> None:
    import faulthandler

    faulthandler.disable()
    # Close the dup file installed during pytest_configure.
    if fault_handler_stderr_key in config._store:
        config._store[fault_handler_stderr_key].close()
        del config._store[fault_handler_stderr_key]
    # Re-enable the faulthandler, attaching it to the default sys.stderr
    # so we can see crashes after pytest has finished, usually during
    # garbage collection during interpreter shutdown.
    faulthandler.enable(file=get_stderr_fileno())


def get_stderr_fileno() -> int:
    try:
        fileno = sys.stderr.fileno()
        # The Twisted Logger will return an invalid file descriptor since it is not backed
        # by an FD. So, let's also forward this to the same code path as with pytest-xdist.
        if fileno == -1:
            raise AttributeError()
        return fileno
    except (AttributeError, io.UnsupportedOperation):
        # pytest-xdist monkeypatches sys.stderr with an object that is not an actual file.
        # https://docs.python.org/3/library/faulthandler.html#issue-with-file-descriptors
        # This is potentially dangerous, but the best we can do.
        return sys.__stderr__.fileno()


def get_timeout_config_value(config: Config) -> float:
    return float(config.getini("faulthandler_timeout") or 0.0)


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_protocol(item: Item) -> Generator[None, None, None]:
    timeout = get_timeout_config_value(item.config)
    stderr = item.config._store[fault_handler_stderr_key]
    if timeout > 0 and stderr is not None:
        import faulthandler

        faulthandler.dump_traceback_later(timeout, file=stderr)
        try:
            yield
        finally:
            faulthandler.cancel_dump_traceback_later()
    else:
        yield


@pytest.hookimpl(tryfirst=True)
def pytest_enter_pdb() -> None:
    """Cancel any traceback dumping due to timeout before entering pdb."""
    import faulthandler

    faulthandler.cancel_dump_traceback_later()


@pytest.hookimpl(tryfirst=True)
def pytest_exception_interact() -> None:
    """Cancel any traceback dumping due to an interactive exception being
    raised."""
    import faulthandler

    faulthandler.cancel_dump_traceback_later()
