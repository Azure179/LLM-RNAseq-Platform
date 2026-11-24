"""Plan/run transport only; source choice and execution live in the shared backend."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from bulk_rnaseq.api.dependencies import get_customization_service
from bulk_rnaseq.api.execution import safe_value
from bulk_rnaseq.api.results import ResultDetail, public_run
from bulk_rnaseq.api.schemas import ErrorResponse
from bulk_rnaseq.api.workflows import ProjectRequest, WorkflowPlanRequest
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.workspace.customization_service import CustomizationService, PreparedCustomization
from bulk_rnaseq.workspace.result_manager import ResultManager


class CustomizationPlanRequest(WorkflowPlanRequest):
    source_run_id: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.:-]*$")


class CustomizationRunRequest(ProjectRequest):
    plan: PreparedCustomization

    @field_validator("plan")
    @classmethod
    def safe_plan(cls, value: PreparedCustomization) -> PreparedCustomization:
        if safe_value(value.model_dump(mode="json")) != value.model_dump(mode="json"):
            raise ValueError("No host paths or secrets in plans")
        return value


class CustomizationPlanResponse(BaseModel):
    status: Literal["ready", "needs_input", "unsupported"]
    plan: PreparedCustomization | None = None
    required_fields: list[str]
    source_options: list[dict[str, str]]
    message: str
    error_code: str | None
    warnings: list[str]
    model: str
    request_count: int
    usage: dict[str, int]
    latency_seconds: float


class CustomizationRunResponse(BaseModel):
    status: str
    project_id: str
    customization_id: str
    source_run_id: str
    mode: str
    changed_parameters: dict[str, Any]
    validation_status: str
    error_code: str | None = None
    message: str | None = None
    result: ResultDetail | None = None


router = APIRouter(prefix="/customizations", tags=["customizations"], responses={
    status: {"model": ErrorResponse} for status in (404, 409, 422, 500, 502, 503, 504)})
Service = Annotated[CustomizationService, Depends(get_customization_service)]


@router.post("/plan", response_model=CustomizationPlanResponse)
def plan_customization(body: CustomizationPlanRequest, service: Service) -> CustomizationPlanResponse:
    """Paid routing/planning only. No Skill or R execution. Review the returned plan."""
    planned = service.plan(body.project_id, body.request, source_run_id=body.source_run_id)
    usage = planned.pop("usage")
    if safe_value(planned) != planned:
        raise SkillError("INVALID_CUSTOMIZATION_PLAN", "Unsafe proposal was rejected.")
    return CustomizationPlanResponse.model_validate({**planned, "usage": usage})


@router.post("/run", response_model=CustomizationRunResponse)
def run_customization(body: CustomizationRunRequest, service: Service) -> CustomizationRunResponse:
    """Execute an unchanged project-local stored plan, rechecking sources. No LLM call.

    Replaying the same completed plan returns its saved report; create a new plan for a new customization.
    """
    report = service.run(body.project_id, body.plan)
    data = {k: report.get(k) for k in ("status", "customization_id", "source_run_id", "mode",
            "changed_parameters", "validation_status", "error_code", "message")}
    data["project_id"] = body.project_id
    run_id = (report.get("skill_result") or {}).get("run_id")
    if run_id:
        browser = ResultManager(service.manager, body.project_id)
        data["result"] = public_run(browser, browser.get_run(run_id)).model_dump(mode="json")
    return CustomizationRunResponse.model_validate(safe_value(data))
