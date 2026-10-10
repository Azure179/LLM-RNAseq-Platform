"""Safe transport/configuration failures, separate from RNA-seq Skill execution results."""

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from bulk_rnaseq.llm.client import ChatResponse


class DeepSeekError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        # Private-to-caller audit context; never included in the safe error JSON.
        self.response: ChatResponse | None = None
        self.prompt_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": "failed", "error_code": self.code, "message": str(self), "details": dict(self.details)}
