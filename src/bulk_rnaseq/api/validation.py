"""Strict HTTP envelopes and host-setting rejection, without a second Skill schema."""

from typing import Any
import math
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_core import PydanticCustomError
from bulk_rnaseq.api.security import private_text
from bulk_rnaseq.exceptions import SkillError


FORBIDDEN_PARAMETERS = frozenset({
    "project_root", "output_dir", "runtime_dir", "runtime", "rscript", "rscript_executable",
    "rscript_path", "python_executable", "executable", "api_key", "deepseek_api_key", "env",
    "environment", "env_file", "shell", "shell_command", "command", "run_id", "workflow_root",
    "temp_dir", "temp_directory", "timeout", "timeout_seconds", "count_matrix_path", "metadata_path",
    "authorization", "password", "token", "credential", "credentials",
})


def check_request_tree(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            # Existing immutable historical identity in an official binding is not
            # a client-selected new execution run_id. No arbitrary paths are allowed.
            historical = key == "run_id" and value.get("source") == "project_result" and "input_bindings" in path
            binding_target = bool(path and path[-1] == "input_bindings" and isinstance(item, dict))
            if str(key).casefold() in FORBIDDEN_PARAMETERS and not historical and not binding_target:
                raise PydanticCustomError("forbidden_parameter", "System-managed parameters cannot be supplied.",
                    {"field": ".".join((*path, str(key)))})
            check_request_tree(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_request_tree(item, (*path, str(index)))
    elif isinstance(value, str) and not value.strip() and path and path[-1] in {"request", "user_request", "project_id", "source_run_id", "run_id"}:
        raise PydanticCustomError("blank_input", "Must not be blank.", {"field": ".".join(path)})
    elif isinstance(value, str) and private_text(value):
        raise PydanticCustomError("unsafe_input", "Use logical identifiers, not paths or credentials.",
                                  {"field": ".".join(path)})
    elif isinstance(value, float) and not math.isfinite(value):
        raise PydanticCustomError("finite_number", "Non-finite numbers are not accepted.")


class ApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def security_boundary(cls, value: Any) -> Any:
        check_request_tree(value)
        return value


def reject_unknown_parameters(parameters: dict[str, Any], schema: dict[str, Any], *, prefix: str = "parameters") -> None:
    """Only identify unknown names. The existing validator still decides values/defaults."""
    unknown = set(parameters) - set(schema.get("properties", {}))
    if unknown:
        raise SkillError("UNKNOWN_PARAMETER", "Unknown business parameter.", details={"fields": [
            {"field": prefix + "." + name, "message": "Unknown parameter."} for name in sorted(unknown)]})
    for key, value in parameters.items():
        if isinstance(value, str) and not value.strip():
            raise SkillError("INVALID_PARAMETER", "Empty parameters are not supported.",
                details={"fields": [{"field": prefix + "." + key, "message": "Must not be blank."}]})


def validate_skill_parameters(parameters: dict[str, Any], schema: dict[str, Any]) -> None:
    reject_unknown_parameters(parameters, schema)
    errors = list(Draft202012Validator(schema).iter_errors(parameters))
    fields = [{"field": "parameters." + ".".join(str(p) for p in error.path),
               "message": "Failed schema constraint: " + str(error.validator)} for error in errors[:30]]
    for name, value in parameters.items():
        if schema["properties"].get(name, {}).get("type") == "integer" and type(value) is not int:
            fields.append({"field": "parameters." + name, "message": "Must be an integer, without coercion."})
    if fields:
        raise SkillError("INVALID_PARAMETER", "Parameters failed existing schema validation.", details={"fields": fields})
