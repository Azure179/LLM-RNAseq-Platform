"""Portable result identities, separate from filenames and UI display records."""

from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


def result_time(value: str | datetime) -> datetime:
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise ValueError("Result timestamps require a timezone")
    return stamp.astimezone(timezone.utc)


class ResultRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    run_id: str
    workflow_id: str | None = None
    step_id: str | None = None
    skill_name: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None
    treatment: str | None = None
    control: str | None = None
    comparison: str = ""
    outputs: dict[str, str] = Field(default_factory=dict)
    parent_run_ids: list[str] = Field(default_factory=list)
    source: str
    time_source: str = "recorded"

    @field_validator("created_at", "completed_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        return result_time(value) if value else None

    @property
    def sort_key(self) -> tuple[datetime, datetime, str]:
        return self.created_at, self.completed_at or self.created_at, self.run_id


class ResultResolution(BaseModel):
    status: Literal["resolved", "ambiguous", "not_found"]
    result: ResultRef | None = None
    candidates: list[ResultRef] = Field(default_factory=list)
    error_code: str | None = None
    current_input_sha256: dict[str, str] | None = None
