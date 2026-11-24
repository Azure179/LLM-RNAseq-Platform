"""Read-only project-scoped metadata; no filesystem path or download input."""

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Path
from pydantic import BaseModel, ConfigDict, Field, AfterValidator, field_validator
from bulk_rnaseq.api.validation import ApiRequest

from bulk_rnaseq.api.dependencies import get_execution_service
from bulk_rnaseq.api.execution import safe_value
from bulk_rnaseq.api.schemas import ErrorResponse, SkillRunRequest
from bulk_rnaseq.workspace.result_manager import ResultManager, RunInfo, display_value
from bulk_rnaseq.workspace.skill_execution import SingleSkillService


class ResultArtifact(BaseModel):
    artifact_id: str
    filename: str
    type: str
    run_id: str
    relative_ref: str | None
    size_bytes: int | None
    error_code: str | None


class ResultDetail(BaseModel):
    project_id: str
    run_id: str
    skill_name: str
    status: str
    result_type: str
    created_at: str
    completed_at: str | None
    time_source: str
    comparison: str
    parameters: dict[str, Any]
    summary: dict[str, Any]
    warnings: list[dict[str, Any]]
    browser_warnings: list[str]
    source_run_id: str | None
    parent_run_ids: list[str]
    workflow_id: str | None
    customization_id: str | None
    step_id: str | None
    dependencies: list[str]
    gene_subset: str | None
    error_code: str | None
    message: str | None
    artifacts: list[ResultArtifact]


class ProjectResults(BaseModel):
    project_id: str
    results: list[ResultDetail]
    collections: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)
    truncated: bool = False


class ResultQuery(ApiRequest):
    # Query strings are textual on the wire; only limit is parsed to int.
    model_config = ConfigDict(extra="forbid", strict=False)
    skill_name: str | None = Field(default=None, min_length=1, pattern=r"^rnaseq\.[a-z][a-z0-9_]*$")
    status: Literal["succeeded", "partial", "failed", "skipped", "unsupported"] | None = None
    result_type: Literal["Analysis", "Workflow", "Customization"] | None = None
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator("limit", mode="before")
    @classmethod
    def decimal_limit(cls, value: Any) -> Any:
        if isinstance(value, str) and not (value.isascii() and value.isdecimal()):
            raise ValueError("Use an integer query value without decimal coercion.")
        return value


def public_run(browser: ResultManager, run: RunInfo) -> ResultDetail:
    # Metadata and lineage already resolved by ResultManager, never inferred here.
    data = {key: value for key, value in asdict(run).items() if key in ResultDetail.model_fields and key != "artifacts"}
    data["project_id"] = browser.project_key
    for key in ("parameters", "summary"):
        data[key] = display_value(data[key])
    artifacts = []
    for item in run.artifacts:
        relative = None
        if item.relative_path and item.error_code is None:
            path = browser.validate_result_path(item.relative_path)
            relative = path.relative_to(browser.project_root).as_posix()
        artifacts.append(dict(artifact_id=item.artifact_id, filename=item.filename, type=item.kind,
            run_id=run.run_id, relative_ref=relative, size_bytes=item.size_bytes, error_code=item.error_code))
    data["artifacts"] = artifacts
    return ResultDetail.model_validate(safe_value(data))


router = APIRouter(tags=["results"], responses={status: {"model": ErrorResponse} for status in (404, 409, 422, 500)})
Service = Annotated[SingleSkillService, Depends(get_execution_service)]
ProjectID = Annotated[str, Path(min_length=1, max_length=80), AfterValidator(SkillRunRequest.valid_project)]
RunID = Annotated[str, Path(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.:-]*$")]


@router.get("/projects/{project_id}/results", response_model=ProjectResults)
def list_results(project_id: ProjectID, service: Service, query: Annotated[ResultQuery, Query()]) -> ProjectResults:
    """Runs plus existing collection envelopes. Limit applies separately to each list.

    Backend statuses are retained (succeeded/partial/failed, etc.). No global result search.
    """
    skill_name, status, result_type, limit = query.skill_name, query.status, query.result_type, query.limit
    browser = ResultManager(service.manager, SkillRunRequest.valid_project(project_id))
    runs = [run for run in browser.list_runs(skill_name=skill_name, status=status)
            if result_type is None or run.result_type == result_type]
    collections = [item for item in browser.list_collections(result_type) if
        (status is None or item["status"] == status) and (skill_name is None or item.get("source_skill") == skill_name
            or any(m.get("skill_name") == skill_name for m in item.get("members", [])))]
    return ProjectResults(project_id=project_id, results=[public_run(browser, run) for run in runs[:limit]],
        collections=safe_value([{k: v for k, v in item.items() if k != "source"} for item in collections[:limit]]),
        warnings=safe_value(browser.diagnostics), truncated=len(runs) > limit or len(collections) > limit)


@router.get("/projects/{project_id}/results/{run_id}", response_model=ResultDetail)
def result_detail(project_id: ProjectID, run_id: RunID, service: Service) -> ResultDetail:
    """Read one unique project-local run, including lineage and safe artifact references.

    Returns metadata only, not file contents. Missing runs return 404; conflicting
    duplicate identities return 409. No analysis or model request is performed.
    """
    browser = ResultManager(service.manager, SkillRunRequest.valid_project(project_id))
    return public_run(browser, browser.get_run(run_id))
