"""Minimal console logging for the project skeleton."""

import logging


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def create_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Create an idempotent console logger with a readable format."""

    logger = logging.getLogger(name)

    try:
        logger.setLevel(level.upper())
    except ValueError as error:
        raise ValueError(f"Unsupported log level: {level}") from error

    # CLI can be called repeatedly in one process with a redirected stderr.
    # Replace only our console handler; never keep a closed stream from an older call.
    for old_handler in tuple(logger.handlers):
        if old_handler.get_name() == "bulk_rnaseq_console":
            logger.removeHandler(old_handler)
            old_handler.close()
    handler = logging.StreamHandler()
    handler.set_name("bulk_rnaseq_console")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)

    logger.propagate = False
    return logger
