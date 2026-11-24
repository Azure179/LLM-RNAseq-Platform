"""API disclosure policy and request-scoped logging; no filesystem mutation."""

from contextvars import ContextVar
import logging
import math
import re
from typing import Any
from urllib.parse import unquote


SECRET_FIELD = re.compile(r"api.?key|secret|token|password|credential|authorization|cookie", re.I)
PRIVATE_FIELD = re.compile(r"api.?key|secret|token|password|credential|authorization|cookie|traceback|stdout|stderr|environment|command|rscript|project_root|runtime_dir|env_file", re.I)
PRIVATE_TEXT = re.compile(
    r"[A-Za-z]:[\\/]|\\|(?<!\w)/[A-Za-z.]|(?:^|[/\s])\.\.(?:/|$)|"
    r"(?<!\w)\.env(?:\b|[._])|(?:^|[/\s])runtime/|sk-[\w-]+|gh[pousr]_[\w]+|"
    r"api.?key|authorization|bearer\s|(?:password|token|secret|credential)\s*[:=]|traceback", re.I)

request_security: ContextVar[tuple[str, tuple[str, ...]] | None] = ContextVar("api_request_security", default=None)


def private_text(text: str) -> bool:
    # Detect escaped/percent-encoded disclosure too; retain actual safe values unchanged.
    decoded = text
    for _ in range(3):
        decoded = unquote(decoded)
    context = request_security.get()
    return bool(PRIVATE_TEXT.search(decoded) or (context and any(secret in text for secret in context[1])))


def safe_value(value: Any) -> Any:
    """Recursively redact diagnostic data; safe artifact relative refs remain intact."""
    if isinstance(value, dict):
        return {key: safe_value(item) for key, item in value.items()
                if isinstance(key, str) and not PRIVATE_FIELD.search(key) and not private_text(key)}
    if isinstance(value, (list, tuple)):
        return [safe_value(item) for item in value]
    if isinstance(value, str):
        return "[redacted]" if private_text(value) else value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or type(value) in (int, float, bool):
        return value
    return "[redacted]"


def install_request_log_redaction() -> None:
    """Install once, active only inside HTTP context, including worker-thread run logs.

    Streamlit/CLI records outside API requests are unchanged. Never copy raw exception
    text into API logs; the HTTP request ID is sufficient to locate persisted runs.
    """
    # Uvicorn logs the raw URL after the request context closes. The middleware
    # already records the matched route, status, duration and generated ID.
    logging.getLogger("uvicorn.access").disabled = True
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_rnaseq_api_redaction", False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        context = request_security.get()
        if context:
            text = record.getMessage()
            if re.search(r"user_input=|\buser=|arguments=|Parsed arguments", text):
                text = "Request payload omitted from API log."
            # This exact API access event is composed exclusively of method,
            # matched route template, status, generated ID and elapsed time.
            access_event = record.name == "bulk_rnaseq.api" and record.msg == "method=%s endpoint=%s status=%s request_id=%s duration_seconds=%.4f"
            record.msg = (text if access_event else str(safe_value(text)))[:1500]
            record.args = ()
            record.exc_info = record.exc_text = record.stack_info = None
            record.msg += " http_request_id=" + context[0]
        return record

    factory._rnaseq_api_redaction = True
    logging.setLogRecordFactory(factory)
