"""Intent envelope only; Skill parameter definitions remain in existing Manifests."""

from typing import Literal, Self
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class StructuredIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    skill_name: str | None
    parameters: dict[str, JsonValue]
    missing_parameters: list[str]
    status: Literal["ready", "needs_input", "unsupported"] = Field(
        description="For a supported task: ready if missing_parameters is empty; needs_input otherwise. Unsupported means no supported task.")
    message: str
    gene_subset: Literal["up", "down", "all"] | None = None
    evidence: dict[str, str] = Field(default_factory=dict, description="Verbatim user quotes for explicit non-default parameters and gene_subset.")

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        if len(set(self.missing_parameters)) != len(self.missing_parameters):
            raise ValueError("Duplicate missing parameters")
        if self.status == "unsupported":
            if self.skill_name is not None or self.parameters or self.missing_parameters or self.gene_subset is not None or self.evidence:
                raise ValueError("Unsupported requests must not advertise a selected executable request")
        else:
            if not self.skill_name:
                raise ValueError("Supported intents require a Skill name")
            if (self.status == "needs_input") != bool(self.missing_parameters):
                raise ValueError("Status must agree with missing parameters")
        if self.status != "ready" and not self.message.strip():
            raise ValueError("Clarification or unsupported explanation is required")
        return self
