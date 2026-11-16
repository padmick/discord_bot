"""Logging that App Platform can read from stderr."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

LOGGER_NAME = "secret_santa"


class _StderrHandler(logging.Handler):
    """Write to the process stderr at emit time.

    A handler that stores sys.stderr at construction misses lines once a test
    runner replaces stderr, and a full buffer hides them from App Platform.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(isinstance(handler, _StderrHandler) for handler in logger.handlers):
        stream = _StderrHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(stream)
    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        file_handler = RotatingFileHandler("secret_santa.log", maxBytes=1_000_000, backupCount=5)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(file_handler)
    return logger


logger = configure_logging()


def log_event(event_type: str, description: str) -> None:
    logger.info("%s: %s", event_type, description)


def root_exception(error: BaseException) -> BaseException:
    """Follow wrapped command errors to the exception that was raised."""
    seen: set[int] = set()
    current = error
    while id(current) not in seen:
        seen.add(id(current))
        nested = getattr(current, "original", None)
        if isinstance(nested, BaseException) and nested is not current:
            current = nested
            continue
        cause = current.__cause__
        if isinstance(cause, BaseException) and cause is not current:
            current = cause
            continue
        break
    return current


def report_failure(where: str, error: BaseException) -> None:
    """Record an unexpected failure, including its traceback, on stderr."""
    cause = root_exception(error)
    logger.error(
        "%s failed: %s: %s",
        where,
        type(cause).__name__,
        cause,
        exc_info=(type(cause), cause, cause.__traceback__),
    )
