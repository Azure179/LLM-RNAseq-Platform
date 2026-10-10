"""Versioned, JSON-safe result envelope; no analysis or execution dispatch."""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Literal


SCHEMA_VERSION = "2.0"
_NAMES = {"differential_expression": "rnaseq.deg", "pca": "rnaseq.pca", "volcano": "rnaseq.volcano",
          "heatmap": "rnaseq.heatmap", "go_enrichment": "rnaseq.go", "kegg_enrichment": "rnaseq.kegg"}
_SCRIPTS = {"differential_expression": "deseq2_deg.R", "pca": "transform_pca.R", "volcano": "plot_volcano.R"}


def skill_name_for(operation: str) -> str:
    return _NAMES.get(operation, "rnaseq." + operation)


def json_copy(value: Any) -> Any:
    """Snapshot JSON data, converting pathlib paths but rejecting NaN/unsupported objects."""
    def encode(item: Any) -> str:
        if isinstance(item, Path):
            return str(item)
        raise TypeError(f"Not JSON serializable: {type(item).__name__}")
    return json.loads(json.dumps(value, default=encode, allow_nan=False, ensure_ascii=False))


def normalize_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for warning in warnings:
        if not isinstance(warning, dict) or not all(isinstance(warning.get(key), str) and warning[key].strip() for key in ("code", "message")):
            raise ValueError("Each warning requires non-empty code and message.")
        details = warning.get("details", {})
        if not isinstance(details, dict):
            raise ValueError("Warning details must be an object.")
        normalized.append({"code": warning["code"], "message": warning["message"],
                           "details": {**{k: v for k, v in warning.items() if k not in {"code", "message", "details"}}, **details}})
    return normalized


@dataclass(frozen=True, slots=True)
class SkillResult:
    status: Literal["succeeded", "failed"]
    skill_name: str
    run_id: str | None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    _legacy: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed"}:
            raise ValueError("status must be succeeded or failed.")
        if not isinstance(self.skill_name, str) or not self.skill_name.startswith("rnaseq.") or not self.skill_name[7:]:
            raise ValueError("skill_name must be a non-empty rnaseq.* identifier.")
        if self.run_id is not None and (not isinstance(self.run_id, str) or not self.run_id.strip()):
            raise ValueError("run_id must be a non-empty string or null before a run exists.")
        if self.status == "succeeded" and self.run_id is None:
            raise ValueError("Successful runs require run_id.")
        for name in ("inputs", "outputs", "summary", "metadata", "details", "_legacy"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError(f"{name} must be an object.")
            object.__setattr__(self, name, json_copy(getattr(self, name)))
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in self.outputs.items()):
            raise ValueError("outputs must map logical names to path strings.")
        if not isinstance(self.warnings, list):
            raise ValueError("warnings must be a list.")
        object.__setattr__(self, "warnings", json_copy(normalize_warnings(self.warnings)))
        if self.status == "failed":
            if not all(isinstance(value, str) and value.strip() for value in (self.error_code, self.message)):
                raise ValueError("Failures require error_code and message.")
            if self.outputs or self.summary:
                raise ValueError("Failures cannot publish successful outputs or metrics.")
        elif self.error_code is not None or self.message is not None or self.details:
            raise ValueError("Successful results cannot contain failure fields.")

    def to_dict(self, *, include_legacy: bool = False) -> dict[str, Any]:
        payload = {"schema_version": SCHEMA_VERSION, "status": self.status, "skill_name": self.skill_name,
                   "run_id": self.run_id, "inputs": self.inputs, "outputs": self.outputs, "summary": self.summary,
                   "warnings": self.warnings, "metadata": self.metadata, "error_code": self.error_code,
                   "message": self.message, "details": self.details}
        if include_legacy:
            payload = {**self._legacy, **payload}
            # Keep old warning extras while the canonical details object is authoritative.
            payload["warnings"] = [{**warning["details"], **warning} for warning in self.warnings]
        return json_copy(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SkillResult":
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported schema version; use from_legacy_summary for Phase 4 data.")
        names = ("status", "skill_name", "run_id", "inputs", "outputs", "summary", "warnings", "metadata", "error_code", "message", "details")
        missing = set(names) - payload.keys()
        if missing:
            raise ValueError(f"Missing result fields: {', '.join(sorted(missing))}")
        return cls(**{key: payload[key] for key in names}, _legacy=payload)

    @classmethod
    def from_legacy_summary(cls, payload: dict[str, Any]) -> "SkillResult":
        """Adapt a Phase 4 summary without rewriting its on-disk file or losing extras."""
        operation = payload["operation"]
        parameters = payload.get("parameters", {})
        inputs = {**payload.get("inputs", {}), "parameters": parameters}
        excluded = {"schema_version", "status", "skill_name", "run_id", "inputs", "outputs", "summary", "warnings", "metadata",
                    "error_code", "message", "details", "error", "metrics", "artifacts"}
        metadata = {key: value for key, value in payload.items() if key not in excluded}
        processes = payload.get("processes")
        if processes is None:
            processes = [payload.get("process", {"return_code": payload.get("return_code"),
                                                   "stdout": payload.get("stdout", ""), "stderr": payload.get("stderr", "")})]
        metadata["processes"] = processes
        provenance = dict(payload.get("provenance", {}))
        scripts = dict(provenance.get("r_scripts_sha256", {}))
        if "r_script_sha256" in provenance:
            scripts[_SCRIPTS.get(operation, "main")] = provenance["r_script_sha256"]
        if "vst_module_sha256" in provenance:
            scripts["vst_common.R"] = provenance["vst_module_sha256"]
        provenance["r_scripts_sha256"] = scripts
        metadata["provenance"] = provenance
        error = payload.get("error") or {}
        output_dir = Path(payload.get("output_dir", "."))
        outputs = {key: str((output_dir / name).resolve()) for key, name in payload.get("artifacts", {}).items()} if payload["status"] == "succeeded" else {}
        return cls(status=payload["status"], skill_name=skill_name_for(operation), run_id=payload.get("run_id"),
                   inputs=inputs, outputs=outputs, summary=payload.get("metrics", {}), warnings=payload.get("warnings", []),
                   metadata=metadata, error_code=error.get("code"), message=error.get("message"), details=error.get("details", {}), _legacy=payload)
