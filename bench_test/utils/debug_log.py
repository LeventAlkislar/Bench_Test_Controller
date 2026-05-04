import logging
import os
import sys


_LOGGER_NAME = "bench_test.debug"


def _debug_log_path() -> str:
    base_dir = (
        os.path.dirname(sys.executable)
        if getattr(sys, "frozen", False)
        else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
    return os.path.join(base_dir, "bench_test_debug.log")


def get_debug_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = logging.FileHandler(_debug_log_path(), delay=True, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    return logger


def debug_log(message: str, level: str = "debug") -> None:
    logger = get_debug_logger()
    log_method = getattr(logger, level.lower(), logger.debug)
    log_method(message)
