"""Thin natural-language transport. Routing and summaries belong to the backend."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse
from bulk_rnaseq.api.errors import assistant_http_error, error_response
from pydantic import BaseModel, Field, field_validator, model_validator

from bulk_rnaseq.api.dependencies import get_assistant_service
from bulk_rnaseq.api.execution import safe_value
from bulk_rnaseq.api.schemas import ErrorResponse
from bulk_rnaseq.api.workflows import WorkflowPlanRequest
from bulk_rnaseq.workspace.assistant_service import AssistantService
from bulk_rnaseq.workspace.chat_controller import ChatResult
from bulk_rnaseq.workspace.result_manager import ResultManager


class AssistantRequest(WorkflowPlanRequest):
    request_id: str | None = Field(default=None, description="Optional canonical UUID for replay protection or resuming a source choice; not a run ID.")
    source_run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$", max_length=160,
        description="Only a candidate returned for the same pending request_id. Keep the original request text.")

    @field_validator("request_id")
    @classmethod
    def valid_request_id(cls, value: str | None) -> str | None:
        if value is not None and str(UUID(value)) != value:
            raise ValueError("Expected canonical UUID")
        return value

    @model_validator(mode="after")
    def source_requires_request(self) -> "AssistantRequest":
        if self.source_run_id is not None and self.request_id is None:
            raise ValueError("Resume a pending request to select a source")
        return self


class AssistantResponse(BaseModel):
    status: Literal["success", "partial_success", "failed", "needs_input", "unsupported"]
    request_type: str
    project_id: str
    request_id: str
    workflow_id: str | None = None
    run_id: str | None = None
    customization_id: str | None = None
    source_run_id: str | None = None
    response: str
    required_fields: list[str] = Field(default_factory=list)
    source_options: list[dict[str, str]] = Field(default_factory=list)
    artifacts: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    stage: str
    final_response_status: str | None = None
    request_count: int
    usage: dict[str, int] = Field(default_factory=dict)
    duration_seconds: float


def public_assistant(result: ChatResult, service: AssistantService, project_id: str) -> AssistantResponse:
    # A successful analysis with a rejected summary is not reported as a reliable answer.
    status = {"succeeded": "success", "partial": "partial_success"}.get(result.status, result.status)
    if result.final_response_status == "failed":
        status = "failed"
    codes = {"SOURCE_SELECTION_REQUIRED": "AMBIGUOUS_UPSTREAM_RESULT",
             "MISSING_PROJECT_INPUTS": "MISSING_PROJECT_INPUT", "UNKNOWN_SKILL": "SKILL_NOT_FOUND",
             "LLM_TOOL_UNKNOWN_NAME": "SKILL_NOT_FOUND", "LLM_TOOL_UNKNOWN_SKILL": "SKILL_NOT_FOUND",
             "INVALID_TOOL_ARGUMENTS": "INVALID_PARAMETER", "LLM_PLANNER_PLAN_INVALID": "WORKFLOW_VALIDATION_FAILED"}
    code = codes.get(result.error_code, result.error_code)
    if code == "MISSING_DEPENDENCY":
        code = "MISSING_UPSTREAM_DEPENDENCY" if any(k in result.missing for k in ("deg_result_file", "gene_list")) else "MISSING_PROJECT_INPUT"
    # Validate artifact references again at the disclosure boundary (including cached chats).
    browser = ResultManager(service.manager, project_id)
    artifacts = []
    for artifact in result.artifacts:
        path = browser.validate_result_path(browser.project_root / artifact["path"])
        artifacts.append({"name": artifact["name"], "path": path.relative_to(browser.project_root).as_posix()})
    # Local diagnostic lines may contain a private path; preserve verified paragraphs.
    text = "\n".join(line if safe_value(line) == line else "本地诊断信息已隐藏，请查看项目日志。"
                     for line in result.assistant_message.splitlines())
    payload = safe_value(dict(status=status, request_type=result.request_type, project_id=project_id,
        request_id=result.request_id, workflow_id=result.workflow_id, run_id=result.run_id,
        customization_id=result.customization_id, source_run_id=result.source_run_id,
        response=text, required_fields=result.missing, source_options=result.source_options,
        artifacts=artifacts, warnings=result.warnings, summary=result.summary, steps=result.steps,
        error_code=code, stage=result.stage, final_response_status=result.final_response_status,
        request_count=result.request_count, duration_seconds=result.duration_seconds))
    payload["usage"] = {key: value for key, value in result.usage.items()
                        if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(value) is int}
    return AssistantResponse.model_validate(payload)


router = APIRouter(prefix="/assistant", tags=["assistant"], responses={
    404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
Service = Annotated[AssistantService, Depends(get_assistant_service)]


@router.post("/analyze", response_model=AssistantResponse, responses={
    status: {"model": ErrorResponse} for status in (409, 502, 503, 504)})
def analyze(body: AssistantRequest, service: Service, request: Request) -> AssistantResponse | JSONResponse:
    """Synchronous, potentially paid analysis. Uses existing project bindings and chat history.

    Business outcomes (including partial/failed analysis) return 200; invalid requests,
    missing projects, request conflicts and unavailable LLM services use HTTP errors.
    Reuse request_id after a network disconnect; do not submit a new ID to retry analysis.
    """
    result = service.analyze(body.project_id, body.request, request_id=body.request_id, source_run_id=body.source_run_id)
    public = public_assistant(result, service, body.project_id)
    code = assistant_http_error(public)
    if code:
        return error_response(request, code, {"assistant_request_id": public.request_id,
            "project_id": body.project_id, "run_id": public.run_id, "workflow_id": public.workflow_id})
    return public
