"""One public exception with a machine-readable failure contract."""

from typing import Any
from bulk_rnaseq.result_schema import SkillResult


class SkillError(RuntimeError):
    """Expected input, execution, or artifact failure; safe for CLI/API consumers."""

    def __init__(
        self, code: str, message: str, *, details: dict[str, Any] | None = None,
        return_code: int | None = None, stdout: str = "", stderr: str = "",
        operation: str = "differential_expression",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        self.run_id: str | None = None
        self.operation = operation
        self.inputs: dict[str, Any] = {}

    def to_skill_result(self) -> SkillResult:
        payload = {"schema_version": "1.0", "operation": self.operation, "run_id": self.run_id,
                   "status": "failed", "inputs": self.inputs, "artifacts": {},
                   "error": {"code": self.code, "message": str(self), "details": self.details},
                   "processes": [{"return_code": self.return_code, "stdout": self.stdout, "stderr": self.stderr}]}
        return SkillResult.from_legacy_summary(payload)

    def to_dict(self) -> dict[str, Any]:
        return self.to_skill_result().to_dict(include_legacy=True)


# Compatibility with Phase 2 imports and except clauses.
DegRunError = SkillError
