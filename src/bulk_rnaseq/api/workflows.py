"""Workflow HTTP transport and safe projections; no execution loop or DAG logic."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from bulk_rnaseq.api.execution import public_result, safe_value
from bulk_rnaseq.api.schemas import ErrorResponse, SkillRunRequest
from bulk_rnaseq.api.dependencies import get_workflow_service
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.workflow.models import WorkflowPlan, WorkflowResult
from bulk_rnaseq.workspace.skill_execution import SkillExecution
from bulk_rnaseq.workspace.workflow_service import WorkflowService
from bulk_rnaseq.api.validation import ApiRequest, reject_unknown_parameters


class ProjectRequest(ApiRequest):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    project_id: str = Field(min_length=1, max_length=80)

    @field_validator("project_id")
    @classmethod
    def valid_project(cls, value: str) -> str:
        return SkillRunRequest.valid_project(value)


class WorkflowPlanRequest(ProjectRequest):
    request: str = Field(min_length=1, max_length=4000)

    @field_validator("request")
    @classmethod
    def safe_request(cls, value: str) -> str:
        if not value.strip() or safe_value(value) != value:
            raise ValueError("Provide task text without local paths or credentials.")
        return value


class WorkflowRunRequest(ProjectRequest):
    plan: WorkflowPlan

    @field_validator("plan")
    @classmethod
    def safe_plan(cls, value: WorkflowPlan) -> WorkflowPlan:
        if safe_value(value.model_dump(mode="json")) != value.model_dump(mode="json"):
            raise ValueError("Plans cannot contain host settings, paths or credentials.")
        return value


class WorkflowPlanResponse(BaseModel):
    status: Literal["ready", "needs_input", "unsupported"]
    plan: WorkflowPlan | None = None
    required_fields: list[str] = Field(default_factory=list)
    message: str = ""
    execution_order: list[str] = Field(default_factory=list)
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    model: str
    usage: dict[str, Any] | None = None
    latency_seconds: float


def public_workflow(result: WorkflowResult, service: WorkflowService, project_id: str) -> WorkflowResult:
    """Preserve the canonical WorkflowResult with safe records and nested SkillResults."""
    payload = result.model_dump(mode="json")
    records, results = {}, {}
    for step_id, record in result.step_records.items():
        safe_record = {key: safe_value(value) for key, value in record.items() if key in {
            "step_id", "skill_name", "status", "run_id", "parameters", "input_bindings", "depends_on",
            "started_at", "finished_at", "skip_reason", "blocked_by", "sample_selection", "output_sha256"}}
        if step_id in result.step_results:
            projected = public_result(SkillExecution(SkillResult.from_dict(result.step_results[step_id]),
                record, result.workflow_id, project_id), service.manager)
            results[step_id] = projected
            safe_record.update(outputs=projected["outputs"], upstream_sources=projected["metadata"]["upstream_sources"],
                source_run_id=projected["metadata"]["source_run_id"], error_code=projected["error_code"], message=projected["message"])
        records[step_id] = safe_record
    payload.update(step_records=records, step_results=results,
                   user_request=safe_value(result.user_request), warnings=safe_value(result.warnings))
    return WorkflowResult.model_validate(payload)


router = APIRouter(prefix="/workflow", tags=["workflow"], responses={
    status: {"model": ErrorResponse, "description": description} for status, description in {
        404: "Project or Skill not found.", 409: "Missing or conflicting dependency / repeated workflow.",
        422: "Malformed request or invalid plan.", 500: "Unexpected internal error.",
        502: "Planner returned an invalid response.", 503: "Planner service unavailable.",
        504: "Planner timed out."}.items()})
Service = Annotated[WorkflowService, Depends(get_workflow_service)]


@router.post("/plan", response_model=WorkflowPlanResponse)
def plan_workflow(body: WorkflowPlanRequest, service: Service) -> WorkflowPlanResponse:
    """One paid DeepSeek planning call; deterministic validation; never execute a Skill."""
    planned = service.plan(body.project_id, body.request)
    payload = {
        "status": planned.decision.status, "plan": planned.plan.model_dump(mode="json") if planned.plan else None,
        "required_fields": planned.decision.missing_parameters, "message": planned.decision.message,
        "execution_order": planned.validation.execution_order if planned.validation else [],
        "dependencies": planned.validation.dependencies if planned.validation else {},
        "warnings": planned.validation.warnings if planned.validation else [],
        "model": planned.response.model, "latency_seconds": planned.response.latency_seconds,
    }
    # Fail closed on model-generated unsafe plan data; never silently change a plan.
    if safe_value(payload) != payload:
        raise SkillError("INVALID_WORKFLOW_PLAN", "Planner response contains unsafe fields.")
    # Usage is structured provider metadata, not a free-form log or model content.
    payload["usage"] = {key: value for key, value in (planned.response.usage or {}).items()
                        if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(value) is int} or None
    return WorkflowPlanResponse.model_validate(payload)


@router.post("/run", response_model=WorkflowResult)
def run_workflow(body: WorkflowRunRequest, service: Service) -> WorkflowResult:
    """Synchronous Mode A only. Completed execution returns 200 even for partial/failed results.

    Revalidates the submitted plan; no Planner call or final LLM summary.
    """
    for step in body.plan.steps:
        schema = service.validator.resolver.contracts.schemas.get(step.skill_name)
        if schema is not None:
            reject_unknown_parameters(step.parameters, schema, prefix="plan.steps." + step.step_id + ".parameters")
    return public_workflow(service.run(body.project_id, body.plan), service, body.project_id)
