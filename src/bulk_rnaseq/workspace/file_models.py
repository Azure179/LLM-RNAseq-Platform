"""Canonical file roles and JSON-safe import/inspection results (not SkillResults)."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FileRole(StrEnum):
    COUNT_MATRIX = "count_matrix"
    METADATA = "metadata"
    GO_MAPPING = "go_mapping"
    KEGG_MAPPING = "kegg_mapping"


class ImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    status: Literal["succeeded", "failed"]
    action: str
    role: FileRole | None = None
    original_filename: str | None = None
    stored_path: str | None = None
    validation: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] | None = None


class FileInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["succeeded", "failed"]
    suggested_role: FileRole | None = None
    candidates: list[FileRole] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    error_code: str | None = None
    message: str = ""
