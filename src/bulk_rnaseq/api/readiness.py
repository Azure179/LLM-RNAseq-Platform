"""Cheap local prerequisites, not a scientific or external-provider health check."""

import os
import shutil
import tempfile
from typing import Literal

from fastapi import Request
from pydantic import BaseModel
from starlette.responses import JSONResponse
from bulk_rnaseq.api.errors import error_response


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, bool]


def readiness(request: Request) -> JSONResponse:
    """Check startup completion, Rscript discovery and writable Workspace; never run R/LLM."""
    state = request.app.state
    checks = {"initialized": bool(getattr(state, "ready", False)),
              "analysis_available": shutil.which(state.rscript_executable) is not None,
              "workspace_writable": False}
    try:
        with tempfile.TemporaryFile(dir=state.workspace_root) as handle:
            handle.write(b"readiness\n")
            handle.flush()
            os.fsync(handle.fileno())
        checks["workspace_writable"] = True
    except OSError:
        pass  # Do not disclose a host path, credential or OS error string.
    ready = all(checks.values())
    if not ready:
        return error_response(request, "SERVICE_NOT_READY", {"checks": checks})
    body = ReadinessResponse(status="ready", checks=checks)
    return JSONResponse(body.model_dump())
