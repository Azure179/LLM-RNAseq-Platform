"""A repeated CLI call must not retain a closed stderr stream."""

from contextlib import redirect_stderr
from io import StringIO

from bulk_rnaseq.logger import create_logger


def test_logger_rebinds_stream_without_duplicate_handlers() -> None:
    old_stream = StringIO()
    with redirect_stderr(old_stream):
        create_logger("test.repeated_cli").info("first call")
    old_stream.close()
    with StringIO() as current_stream, redirect_stderr(current_stream):
        logger = create_logger("test.repeated_cli")
        logger.error("second call")
        assert current_stream.getvalue().count("second call") == 1
        assert "Traceback" not in current_stream.getvalue()
        assert len([handler for handler in logger.handlers if handler.name == "bulk_rnaseq_console"]) == 1
