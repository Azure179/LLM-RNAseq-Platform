"""UI-readable project errors; project management is not an RNA-seq Skill."""

from typing import Any


class ProjectError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.details = code, details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"status": "failed", "error_code": self.code, "message": str(self), "details": dict(self.details)}
