"""Local metadata, Skill and Workflow service. Start with uvicorn bulk_rnaseq.api.main:app."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from bulk_rnaseq import __version__
from bulk_rnaseq.config import load_runtime_config
from bulk_rnaseq.api.routes import router
from bulk_rnaseq.api.workflows import router as workflow_router
from bulk_rnaseq.api.assistant import router as assistant_router
from bulk_rnaseq.api.results import router as results_router
from bulk_rnaseq.api.customizations import router as customizations_router
from bulk_rnaseq.api.schemas import ErrorResponse
from bulk_rnaseq.api.errors import error_response, domain_error, validation_error
from bulk_rnaseq.api.security import SECRET_FIELD, request_security, install_request_log_redaction
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.logger import create_logger
from bulk_rnaseq.workspace import ProjectManager, ProjectError
from bulk_rnaseq.workspace.skill_execution import SingleSkillService
from bulk_rnaseq.workspace.workflow_service import WorkflowService
from bulk_rnaseq.workspace.assistant_service import AssistantService
from bulk_rnaseq.workspace.customization_service import CustomizationService
from bulk_rnaseq.llm.exceptions import DeepSeekError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.logger = create_logger("bulk_rnaseq.api")
    install_request_log_redaction()
    app.state.skill_registry = create_default_registry()
    resources = files("bulk_rnaseq.manifests").joinpath("schemas")
    app.state.schema_documents = {
        "schemas/" + name: json.loads(resources.joinpath(name).read_text(encoding="utf-8"))
        for name in ("input-types.schema.json", "skill-result.schema.json")
    }
    app.state.execution_service = SingleSkillService(app.state.skill_registry, ProjectManager(app.state.workspace_root),
        rscript_executable=app.state.rscript_executable, timeout=app.state.skill_timeout)
    app.state.workflow_service = WorkflowService(app.state.skill_registry, app.state.execution_service.manager,
        rscript_executable=app.state.rscript_executable, timeout=app.state.skill_timeout, env_file=app.state.llm_env_file)
    app.state.assistant_service = AssistantService(app.state.execution_service, app.state.workflow_service)
    app.state.customization_service = CustomizationService(app.state.skill_registry, app.state.execution_service.manager,
        env_file=app.state.llm_env_file, rscript_executable=app.state.rscript_executable, timeout=app.state.skill_timeout)
    # Documentation is projected from the same backend parameter policy, not hand-written.
    document = app.openapi()
    operation = document["paths"]["/skills/{skill_name}/run"]["post"]
    operation["x-skill-parameters"] = {name: app.state.execution_service.parameter_schema(name)
                                     for name in app.state.skill_registry.list_skills()}
    # Render the generated business choices in Swagger as well as the machine-readable extension.
    document["components"]["schemas"]["SkillRunRequest"]["properties"]["parameters"].update(
        anyOf=[{"title": name, **schema} for name, schema in operation["x-skill-parameters"].items()])
    app.state.logger.info("API started; Skill execution and separate Workflow planning/execution enabled")
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        app.state.logger.info("API stopped")
        del app.state.skill_registry
        del app.state.schema_documents
        del app.state.execution_service
        del app.state.workflow_service
        del app.state.assistant_service
        del app.state.customization_service


def create_app(*, workspace_root: Path | None = None, rscript_executable: str | None = None, timeout: float = 600,
               llm_env_file: Path | None = None) -> FastAPI:
    """Configure host settings; startup does not run analysis or contact an LLM."""
    application = FastAPI(title="LLM-driven Bulk RNA-seq API", version=__version__,
        description="Phase 8: synchronous Skill, Workflow, Assistant, Results and controlled Customization services. Shared backend, unified errors and disclosure protection. Local / trusted environment only; no authentication.",
        lifespan=lifespan, responses={s: {"model": ErrorResponse} for s in (400, 404, 409, 422, 500)})
    settings = load_runtime_config()
    application.state.workspace_root = workspace_root or settings.workspace_root
    application.state.ready = False
    application.state.rscript_executable, application.state.skill_timeout = rscript_executable or settings.rscript_executable, timeout
    application.state.llm_env_file = llm_env_file

    @application.exception_handler(SkillError)
    @application.exception_handler(ProjectError)
    async def skill_error(request: Request, error: SkillError | ProjectError) -> JSONResponse:
        return domain_error(request, error)

    @application.exception_handler(DeepSeekError)
    async def planner_error(request: Request, error: DeepSeekError) -> JSONResponse:
        return domain_error(request, error)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        if error.status_code >= 500:
            return error_response(request, "INTERNAL_SERVER_ERROR")
        code = {400: "INVALID_REQUEST", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(error.status_code, "HTTP_ERROR")
        response = error_response(request, code, status=error.status_code)
        if error.headers and "Allow" in error.headers:
            methods = {part.strip() for part in error.headers["Allow"].split(",")}
            if methods <= {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}:
                response.headers["Allow"] = ", ".join(sorted(methods))
        return response

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return validation_error(request, error.errors())

    @application.middleware("http")
    async def log_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid4())  # Never trust a supplied header for log content.
        request.state.request_id = request_id
        started = perf_counter()
        secrets = [value for key, value in os.environ.items() if SECRET_FIELD.search(key) and len(value) >= 4]
        authorization = request.headers.get("Authorization", "")
        if authorization:
            secrets.extend([authorization, authorization.split(" ")[-1]])
        scope = request_security.set((request_id, tuple(value for value in secrets if len(value) >= 4)))
        try:
            try:
                response = await call_next(request)
            except Exception as error:
                request.app.state.logger.error("API failure request_id=%s type=%s", request_id, type(error).__name__)
                response = error_response(request, "INTERNAL_SERVER_ERROR")
            response.headers["X-Request-ID"] = request_id
            route = request.scope.get("route")
            request.app.state.logger.info("method=%s endpoint=%s status=%s request_id=%s duration_seconds=%.4f",
                request.method, getattr(route, "path", "<unmatched>"), response.status_code, request_id, perf_counter() - started)
            return response
        finally:
            request_security.reset(scope)

    application.include_router(router)
    application.include_router(workflow_router)
    application.include_router(assistant_router)
    application.include_router(results_router)
    application.include_router(customizations_router)
    return application


app = create_app()
