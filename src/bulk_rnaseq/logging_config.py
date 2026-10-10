"""Per-run file logging; handlers are always closed, including on Windows."""

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from pathlib import Path

from bulk_rnaseq.logger import LOG_FORMAT


@contextmanager
def run_logger(path: Path, run_id: str) -> Iterator[logging.Logger]:
    # An independent Logger avoids accumulating names/handlers in the global registry.
    logger = logging.Logger(f"bulk_rnaseq.run.{run_id}", level=logging.INFO)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    logger.propagate = False
    try:
        yield logger
    finally:
        logger.removeHandler(handler)
        handler.close()
