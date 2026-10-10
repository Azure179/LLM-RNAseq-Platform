"""Portable, path-free plan references; actual SkillResults retain their existing schema."""

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.workspace.models import validate_name

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$", max_length=160)]
GeneSubset = Literal["all", "up", "down"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ProjectInputBinding(Model):
    source: Literal["project_input"] = "project_input"
    key: Identifier


class StepOutputBinding(Model):
    source: Literal["workflow_step"] = "workflow_step"
    step_id: Identifier
    output: Identifier
    gene_subset: GeneSubset | None = None


class ProjectResultBinding(Model):
    source: Literal["project_result"] = "project_result"
    run_id: Identifier
    output: Identifier
    gene_subset: GeneSubset | None = None


InputBinding = Annotated[ProjectInputBinding | StepOutputBinding | ProjectResultBinding,
                         Field(discriminator="source")]


class WorkflowStep(Model):
    step_id: Identifier
    skill_name: Annotated[str, Field(pattern=r"^rnaseq\.[a-z][a-z0-9_]*$")]
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    depends_on: list[Identifier] = Field(default_factory=list)
    input_bindings: dict[str, InputBinding] = Field(default_factory=dict)
    output_alias: Identifier | None = None


class WorkflowPlan(Model):
    schema_version: Literal["1.0"] = "1.0"
    workflow_id: Identifier = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    user_request: str = Field(min_length=1, max_length=16000)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=200)
    status: Literal["planned"] = "planned"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings: list[str] = Field(default_factory=list)

    @field_validator("project_id")
    @classmethod
    def portable_project_key(cls, value: str) -> str:
        return validate_name(value)

    @model_validator(mode="after")
    def unique_steps(self) -> Self:
        ids = [step.step_id for step in self.steps]
        aliases = [step.output_alias for step in self.steps if step.output_alias is not None]
        if len(ids) != len(set(ids)) or len(aliases) != len(set(aliases)):
            raise ValueError("Step IDs and non-null output aliases must be unique")
        if datetime.fromisoformat(self.created_at).tzinfo is None:
            raise ValueError("created_at must include timezone")
        return self


class PlanIssue(Model):
    code: str
    message: str
    step_id: str | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)


class PlanValidationResult(Model):
    valid: bool
    validated_plan: WorkflowPlan | None = None
    execution_order: list[str] = Field(default_factory=list)
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    resolved_bindings: dict[str, dict[str, InputBinding]] = Field(default_factory=dict)
    resolved_parameters: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    errors: list[PlanIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    scope: Literal["dependency_contract_only", "project_files_and_dependency_contract"] = "dependency_contract_only"


class WorkflowResult(Model):
    """Execution envelope; existing canonical statuses remain backward compatible."""

    schema_version: Literal["1.0"] = "1.0"
    workflow_id: Identifier
    status: Literal["succeeded", "failed", "partial"]
    execution_order: list[Identifier] = Field(default_factory=list)
    user_request: str = ""
    step_records: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    completed_steps: list[Identifier] = Field(default_factory=list)
    failed_steps: list[Identifier] = Field(default_factory=list)
    skipped_steps: list[Identifier] = Field(default_factory=list)
    step_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    outputs: dict[str, StepOutputBinding] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("step_results")
    @classmethod
    def canonical_results(cls, value: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        # Validate with the existing SkillResult, not a second per-Skill definition.
        try:
            return {key: SkillResult.from_dict(result).to_dict() for key, result in value.items()}
        except (TypeError, OverflowError) as cause:
            raise ValueError("Malformed canonical SkillResult") from cause

    @model_validator(mode="after")
    def consistent_results(self) -> Self:
        all_ids = self.completed_steps + self.failed_steps + self.skipped_steps
        if not all_ids or len(all_ids) != len(set(all_ids)):
            raise ValueError("Result step lists must be non-empty in total and disjoint")
        if self.execution_order and (len(self.execution_order) != len(all_ids) or set(self.execution_order) != set(all_ids)):
            raise ValueError("Execution order must contain every step exactly once")
        if set(self.step_results) != set(self.completed_steps + self.failed_steps):
            raise ValueError("Every completed/failed step requires its own SkillResult")
        for step, result in self.step_results.items():
            expected = "succeeded" if step in self.completed_steps else "failed"
            if result["status"] != expected:
                raise ValueError("Step classification differs from SkillResult status")
        expected_status = ("succeeded" if not self.failed_steps and not self.skipped_steps
                           else "partial" if self.completed_steps else "failed")
        if self.status != expected_status:
            raise ValueError("Workflow status differs from step outcomes")
        for binding in self.outputs.values():
            if (binding.step_id not in self.completed_steps or binding.gene_subset is not None
                    or binding.output not in self.step_results[binding.step_id]["outputs"]):
                raise ValueError("Workflow outputs must reference actual successful artifacts")
        return self
