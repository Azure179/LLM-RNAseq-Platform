"""HTTP transport delegates metadata and single-Skill requests to existing services."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from bulk_rnaseq.api.dependencies import get_skill_registry, get_execution_service
from bulk_rnaseq.api.schemas import HealthResponse, SkillDetail, SkillMetadata, SkillsResponse, SkillRunRequest, ErrorResponse
from bulk_rnaseq.api.execution import SkillResultResponse, public_result
from bulk_rnaseq.workspace.skill_execution import SingleSkillService
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.api.errors import error_response
from bulk_rnaseq.api.validation import validate_skill_parameters
from bulk_rnaseq.api.readiness import ReadinessResponse, readiness


router = APIRouter()
router.add_api_route("/ready", readiness, methods=["GET"], response_model=ReadinessResponse,
                     responses={503: {"model": ErrorResponse}}, tags=["health"])
RegistryDependency = Annotated[SkillRegistry, Depends(get_skill_registry)]


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Process liveness only; not an R, data, or DeepSeek readiness check."""
    return HealthResponse()


@router.get("/skills", response_model=SkillsResponse, tags=["skills"])
def list_skills(registry: RegistryDependency) -> SkillsResponse:
    """Registered descriptions, including explicitly unavailable entries."""
    return SkillsResponse(skills=[SkillMetadata.model_validate(registry.get_manifest(name))
        for name in registry.list_skills(available_only=False)])


@router.get("/skills/{skill_name}", response_model=SkillDetail, tags=["skills"])
def skill_detail(skill_name: str, request: Request, registry: RegistryDependency) -> SkillDetail:
    """Public Manifest fields and original referenced JSON Schema documents."""
    return SkillDetail.model_validate({**registry.get_manifest(skill_name),
                                      "schema_documents": request.app.state.schema_documents})


@router.post("/skills/{skill_name}/run", response_model=SkillResultResponse, tags=["skills"],
    responses={status: {"model": ErrorResponse, "description": description} for status, description in {
        404: "Skill, project or source not found.", 409: "Missing or ambiguous dependency / project conflict.",
        422: "Invalid request, parameters or analysis input.", 500: "Unexpected internal error.",
        502: "R process failed.", 503: "R runtime unavailable.", 504: "R execution timed out."}.items()})
def run_skill(skill_name: str, body: SkillRunRequest, request: Request,
              service: Annotated[SingleSkillService, Depends(get_execution_service)]) -> JSONResponse:
    """Synchronous: one request runs at most one registered Skill, without LLM calls."""
    validate_skill_parameters(body.parameters, service.parameter_schema(skill_name))
    outcome = service.execute(skill_name, body.project_id, body.parameters, source_run_id=body.source_run_id)
    payload = public_result(outcome, service.manager)
    if outcome.result.status != "succeeded":
        return error_response(request, outcome.result.error_code, {"run_id": payload["run_id"],
            "workflow_id": payload["metadata"]["workflow_id"], "project_id": body.project_id})
    return JSONResponse(content=payload)
