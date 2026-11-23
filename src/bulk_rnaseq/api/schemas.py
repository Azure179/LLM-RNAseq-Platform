"""Small HTTP envelopes; Skill parameter definitions remain in the Manifests."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from bulk_rnaseq import __version__
from bulk_rnaseq.api.validation import ApiRequest


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "llm-rnaseq-platform"
    version: str = __version__


class SkillMetadata(BaseModel):
    # Projection: do not serialize entry_point, executors, or future internal fields.
    model_config = ConfigDict(extra="ignore")

    name: str
    version: str
    description: str
    category: str
    availability: dict[str, Any]
    dependencies: dict[str, Any]


class SkillsResponse(BaseModel):
    skills: list[SkillMetadata]


class SkillDetail(SkillMetadata):
    input_schema: dict[str, Any] | bool
    output_schema: dict[str, Any] | bool
    limitations: list[str]
    use_when: list[str]
    do_not_use_when: list[str]
    raw_count_requirement: str
    # Existing referenced documents, keyed by their exact bundled reference URI.
    # No new manually maintained parameter schema, filesystem URI or network fetch.
    schema_documents: dict[str, dict[str, Any]]


class ApiError(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str
    message: str
    details: dict[str, Any] | list[dict[str, Any]] | None = None
    request_id: str


class ApiErrorResponse(BaseModel):
    error: ApiError


# Import compatibility, one actual OpenAPI model.
ErrorDetail = ApiError
ErrorResponse = ApiErrorResponse


class SkillRunRequest(ApiRequest):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    project_id: str = Field(min_length=1, max_length=80)
    parameters: dict[str, JsonValue] = Field(default_factory=dict,
        description="Business parameters only. See x-skill-parameters on this operation for each Skill's generated schema.")
    source_run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$", max_length=160,
        description="Optional existing result identity within this project; never a new run ID or filesystem path.")

    @field_validator("project_id")
    @classmethod
    def valid_project(cls, value: str) -> str:
        from bulk_rnaseq.workspace.models import validate_name
        return validate_name(value)
