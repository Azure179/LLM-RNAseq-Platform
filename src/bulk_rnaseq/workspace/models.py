"""Versioned portable metadata and runtime-only absolute-path snapshots."""

from dataclasses import dataclass
from datetime import datetime
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from bulk_rnaseq.workspace.file_models import FileRole


PROJECT_DIRS = ("input", "results", "logs", "reports", "runtime")
FILE_ROLES = tuple(role.value for role in FileRole)
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$",
             *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³")}


def validate_name(value: str) -> str:
    """Portable Windows/POSIX project name, also used for immutable folder keys."""
    if (not isinstance(value, str) or not value or len(value) > 80 or value != value.strip()
            or value.startswith(".") or value.endswith(".") or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', value)
            or value.split(".", 1)[0].rstrip(" .").upper() in _RESERVED):
        raise ValueError("Use a non-empty portable project name (up to 80 characters).")
    return value


def validate_relative_input(value: str) -> str:
    """Persist only canonical POSIX relative references beneath input/."""
    if not isinstance(value, str) or "\\" in value or PureWindowsPath(value).drive:
        raise ValueError("Expected project-relative input path")
    path = PurePosixPath(value)
    if (path.is_absolute() or len(path.parts) < 2 or path.parts[0] != "input"
            or value != path.as_posix() or ".." in path.parts):
        raise ValueError("Expected project-relative input path")
    for part in path.parts:
        validate_name(part)
    return value


class ProjectFiles(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    count_matrix: str | None = None
    metadata: str | None = None
    go_mapping: str | None = None
    kegg_mapping: str | None = None

    @field_validator("*")
    @classmethod
    def relative_paths(cls, value: str | None) -> str | None:
        return validate_relative_input(value) if value is not None else None


class ProjectMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    project_name: str
    created_at: str
    updated_at: str
    description: str = Field(default="", max_length=4000)
    files: ProjectFiles = Field(default_factory=ProjectFiles)
    # Reserved until import/run management defines their contract. Not arbitrary
    # JSON bags: otherwise absolute paths or credentials could leak into metadata.
    analysis: dict[str, Any] = Field(default_factory=dict)
    last_run: None = None

    @field_validator("project_name")
    @classmethod
    def name(cls, value: str) -> str:
        return validate_name(value)

    @field_validator("analysis")
    @classmethod
    def reserved(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value:
            raise ValueError("Analysis state is reserved for a later phase")
        return value

    @model_validator(mode="after")
    def timestamps(self) -> Self:
        created, updated = (datetime.fromisoformat(value) for value in (self.created_at, self.updated_at))
        if created.tzinfo is None or updated.tzinfo is None or updated < created:
            raise ValueError("Expected ordered timezone-aware timestamps")
        return self


@dataclass(frozen=True)
class ProjectSnapshot:
    key: str  # Immutable initial directory name, independent of display name.
    root: Path
    metadata: ProjectMetadata


@dataclass(frozen=True)
class ProjectContext:
    """Host-only paths; not an LLM payload and not an execution request."""
    project_key: str
    project_name: str
    project_root: Path
    input_root: Path
    output_root: Path
    logs_root: Path
    reports_root: Path
    runtime_root: Path
    count_matrix: Path | None
    metadata: Path | None
    go_mapping: Path | None
    kegg_mapping: Path | None
