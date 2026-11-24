"""Single HTTP error policy. Domain models and statistical statuses remain unchanged."""

from typing import Any
from fastapi import Request
from starlette.responses import JSONResponse
from bulk_rnaseq.api.schemas import ApiError, ApiErrorResponse
from bulk_rnaseq.api.security import safe_value

ERRORS = {
    "SERVICE_NOT_READY": (503, "SERVICE_NOT_READY"),
    "LEGACY_PATH_UNAVAILABLE": (409, "LEGACY_PATH_UNAVAILABLE"),
    "PROJECT_RESOURCE_MISSING": (409, "PROJECT_RESOURCE_MISSING"),
    "ARTIFACT_NOT_FOUND": (404, "ARTIFACT_NOT_FOUND"),
    "FORBIDDEN_PARAMETER": (422, "FORBIDDEN_PARAMETER"),
    "UNKNOWN_PARAMETER": (422, "UNKNOWN_PARAMETER"),
    "INVALID_REQUEST": (422, "INVALID_REQUEST"),
    "REQUEST_ID_CONFLICT": (409, "REQUEST_ID_CONFLICT"),
    "REQUEST_ALREADY_SUBMITTED": (409, "REQUEST_ALREADY_SUBMITTED"),
    "INVALID_SOURCE_SELECTION": (409, "INVALID_SOURCE_SELECTION"),
    "RUN_NOT_FOUND": (404, "RESULT_NOT_FOUND"),
    "DUPLICATE_RUN_ID": (409, "AMBIGUOUS_UPSTREAM_RESULT"),
    "INVALID_CUSTOMIZATION_PLAN": (422, "INVALID_CUSTOMIZATION_PLAN"),
    "INVALID_CUSTOMIZATION_PARAMETERS": (422, "INVALID_PARAMETER"),
    "CUSTOMIZATION_SOURCE_MISMATCH": (422, "INVALID_PARAMETER"),
    "CUSTOMIZATION_ALREADY_SUBMITTED": (409, "CUSTOMIZATION_ALREADY_SUBMITTED"),
    "CHAT_BUSY": (409, "PROJECT_BUSY"),
    "INVALID_WORKFLOW_PLAN": (422, "INVALID_WORKFLOW_PLAN"),
    "WORKFLOW_CYCLE_DETECTED": (422, "WORKFLOW_CYCLE_DETECTED"),
    "INVALID_OUTPUT_BINDING": (422, "INVALID_OUTPUT_BINDING"),
    "INVALID_INPUT_BINDING": (422, "INVALID_INPUT_BINDING"),
    "INCOMPATIBLE_INPUT_BINDING": (422, "INCOMPATIBLE_INPUT_BINDING"),
    "INCOMPATIBLE_OUTPUT_BINDING": (422, "INCOMPATIBLE_OUTPUT_BINDING"),
    "INVALID_EXECUTION_DEPENDENCY": (422, "INVALID_EXECUTION_DEPENDENCY"),
    "UNKNOWN_WORKFLOW_STEP": (422, "UNKNOWN_WORKFLOW_STEP"),
    "PROJECT_CONTEXT_MISMATCH": (422, "INVALID_WORKFLOW_PLAN"),
    "INVALID_WORKFLOW_ID": (422, "INVALID_WORKFLOW_PLAN"),
    "WORKFLOW_ALREADY_EXISTS": (409, "WORKFLOW_ALREADY_EXISTS"),
    "UNKNOWN_SKILL": (404, "SKILL_NOT_FOUND"), "PROJECT_NOT_FOUND": (404, "PROJECT_NOT_FOUND"),
    "RESULT_NOT_FOUND": (404, "RESULT_NOT_FOUND"),
    "SKILL_UNAVAILABLE": (409, "SKILL_UNAVAILABLE"),
    "MISSING_PROJECT_INPUT": (409, "MISSING_PROJECT_INPUT"),
    "MISSING_UPSTREAM_DEPENDENCY": (409, "MISSING_UPSTREAM_DEPENDENCY"),
    "AMBIGUOUS_UPSTREAM_RESULT": (409, "AMBIGUOUS_UPSTREAM_RESULT"),
    "INVALID_WORKFLOW_PARAMETERS": (422, "INVALID_PARAMETER"),
    "INVALID_PARAMETER": (422, "INVALID_PARAMETER"),
    "INVALID_PROJECT_NAME": (422, "INVALID_PARAMETER"),
    "INVALID_COMPARISON": (422, "INVALID_COMPARISON"),
    "IDENTICAL_GROUPS": (422, "INVALID_COMPARISON"), "GROUP_NOT_FOUND": (422, "INVALID_COMPARISON"),
    "RSCRIPT_FAILED": (502, "RSCRIPT_FAILED"), "RSCRIPT_TIMEOUT": (504, "RSCRIPT_TIMEOUT"),
    "RSCRIPT_NOT_FOUND": (503, "RSCRIPT_NOT_FOUND"), "RSCRIPT_START_FAILED": (503, "RSCRIPT_START_FAILED"),
}
INPUT_ERRORS = frozenset({"INVALID_INPUT", "SAMPLE_MISMATCH", "NON_INTEGER_COUNT", "NEGATIVE_COUNT", "MISSING_VALUE",
    "COUNT_FORMAT_ERROR", "METADATA_FORMAT_ERROR", "GROUP_COLUMN_NOT_FOUND", "INSUFFICIENT_REPLICATES",
    "NO_ANALYZABLE_GENES", "ZERO_LIBRARY", "DUPLICATE_ID", "COUNT_NOT_NUMERIC", "COUNT_NOT_FINITE",
    "COUNT_OUT_OF_RANGE", "EMPTY_GENE_SUBSET", "INSUFFICIENT_SAMPLES", "INSUFFICIENT_GENES", "FILE_NOT_FOUND"})
INPUT_ERRORS |= frozenset({"INPUT_NOT_FILE", "INPUT_READ_ERROR", "INVALID_IDENTIFIER", "UNSUPPORTED_GROUPS",
    "DEG_VALUE_INVALID", "DEG_FORMAT_ERROR", "DEG_COLUMNS_MISSING", "DEG_EMPTY",
    "EXPRESSION_FORMAT_ERROR", "EXPRESSION_VALUE_INVALID", "NO_COMMON_GENES", "NO_SIGNIFICANT_GENES", "NO_PLOTTABLE_GENES",
    "MAPPING_FORMAT_ERROR", "MAPPING_SHEET_REQUIRED", "MAPPING_SHEET_NOT_FOUND", "GENE_LIST_FORMAT_ERROR",
    "GENE_LIST_EMPTY", "GENES_OUTSIDE_UNIVERSE", "NO_MAPPED_GENES", "ONTOLOGY_INFORMATION_REQUIRED", "BACKGROUND_REQUIRED",
    "INVALID_GO_ID", "INVALID_PATHWAY_ID", "UNSUPPORTED_PATHWAY_NAMESPACE", "PATHWAY_MAPPING_REQUIRED",
    "MIXED_PATHWAY_NAMESPACES", "MAPPING_CONFLICT", "NO_PATHWAY_ASSOCIATIONS"})
CONFLICTS = frozenset({"STALE_DEG_RESULT", "DEG_INPUT_VERSION_UNVERIFIED", "IMPORTED_FILE_CHANGED", "IMPORT_RECORD_INVALID", "PROJECT_INPUT_CHANGED", "PROJECT_CHANGED",
    "UPSTREAM_ARTIFACT_CHANGED", "WORKFLOW_INPUT_CHANGED", "WORKFLOW_REVALIDATION_FAILED", "WORKFLOW_VALIDATION_CHANGED",
    "PROJECT_PATH_NOT_ALLOWED", "WORKSPACE_BUSY", "UPSTREAM_PROVENANCE_MISSING", "UPSTREAM_REFERENCE_MISMATCH",
    "AMBIGUOUS_INPUT_BINDING", "CONFLICTING_INPUT_BINDINGS"})


def http_error(code: str | None) -> tuple[int, str]:
    if code in INPUT_ERRORS:
        return 422, "INPUT_VALIDATION_FAILED"
    if code in CONFLICTS:
        return 409, code
    if code in {"LLM_TIMEOUT"}:
        return 504, code
    if code in {"LLM_PLANNER_INPUT_ERROR", "LLM_INPUT_ERROR", "CUSTOMIZATION_INPUT_ERROR"}:
        return 422, code
    if code in {"LLM_KEY_MISSING", "LLM_CONFIG_ERROR", "LLM_AUTH_ERROR", "LLM_INSUFFICIENT_BALANCE",
                "LLM_RATE_LIMIT", "LLM_NETWORK_ERROR", "LLM_FORBIDDEN", "LLM_SERVICE_ERROR"}:
        return 503, code
    if code in {"LLM_RESPONSE_VALIDATION_FAILED", "LLM_ROUTE_INVALID", "LLM_PLANNER_PLAN_INVALID",
                "LLM_PLANNER_RESPONSE_INVALID", "CUSTOMIZATION_RESPONSE_INVALID", "LLM_RESPONSE_ERROR",
                "LLM_JSON_ERROR", "LLM_BAD_REQUEST", "LLM_NOT_FOUND", "LLM_HTTP_ERROR", "LLM_RESPONSE_INVALID",
                "LLM_PLANNER_BINDING_ERROR", "LLM_PLANNER_INCOMPLETE", "LLM_PLANNER_JSON_ERROR",
                "LLM_PLANNER_PARAMETER_ERROR", "LLM_PLANNER_SCHEMA_ERROR", "LLM_PLANNER_UNSAFE_RESPONSE", "LLM_INCOMPLETE_RESPONSE"}:
        return 502, code
    return ERRORS.get(code, (500, "INTERNAL_SERVER_ERROR"))


MESSAGES = {
    "LEGACY_PATH_UNAVAILABLE": "Legacy absolute paths cannot be resolved on this host. Explicit migration is required.",
    "PROJECT_RESOURCE_MISSING": "A project resource is missing; restore or re-import it.",
    "INTERNAL_SERVER_ERROR": "An internal server error occurred.",
    "PROJECT_NOT_FOUND": "The requested project was not found.",
    "SKILL_NOT_FOUND": "The requested Skill was not found.",
    "RESULT_NOT_FOUND": "The requested result was not found in this project.",
    "ARTIFACT_NOT_FOUND": "The requested artifact was not found in this project.",
    "FORBIDDEN_PARAMETER": "System-managed parameters cannot be supplied.",
    "UNKNOWN_PARAMETER": "Unknown parameters are not allowed.",
    "INVALID_REQUEST": "Request validation failed.",
    "INVALID_PARAMETER": "Business parameters failed validation.",
    "INVALID_WORKFLOW_PLAN": "Workflow plan failed validation.",
    "INVALID_CUSTOMIZATION_PLAN": "Customization plan failed validation.",
    "MISSING_PROJECT_INPUT": "Required project inputs are missing.",
    "STALE_DEG_RESULT": "The saved DEG does not match the current inputs or requested DEG settings. Run DEG again or explicitly select a historical run.",
    "DEG_INPUT_VERSION_UNVERIFIED": "The available records cannot verify a DEG for the current project inputs.",
    "MISSING_UPSTREAM_DEPENDENCY": "A compatible existing upstream result is required.",
    "AMBIGUOUS_UPSTREAM_RESULT": "Choose an explicit source_run_id from the candidates.",
}


def error_response(request: Request, code: str, details: Any = None, *, status: int | None = None) -> JSONResponse:
    mapped_status, public_code = http_error(code)
    if status is not None:  # Framework 400/404/405, never a domain-specific router mapping.
        mapped_status, public_code = status, code
    clean = safe_value(details) if mapped_status != 500 else None
    payload = ApiErrorResponse(error=ApiError(code=public_code,
        message=MESSAGES.get(public_code, "Request could not be completed; inspect the error code."),
        details=clean, request_id=request.state.request_id))
    return JSONResponse(status_code=mapped_status, content=payload.model_dump())


def domain_error(request: Request, error: Any) -> JSONResponse:
    details = {key: value for key, value in getattr(error, "details", {}).items()
               if key in {"candidates", "missing", "rules", "errors", "fields"}}
    return error_response(request, error.code, details or None)


def validation_error(request: Request, errors: list[dict[str, Any]]) -> JSONResponse:
    kinds = {item["type"] for item in errors}
    code = "FORBIDDEN_PARAMETER" if "forbidden_parameter" in kinds else "UNKNOWN_PARAMETER" if "extra_forbidden" in kinds else "INVALID_REQUEST"
    if code == "INVALID_REQUEST" and any(e["loc"][:2] == ("body", "plan") for e in errors):
        code = "INVALID_WORKFLOW_PLAN" if request.url.path == "/workflow/run" else "INVALID_CUSTOMIZATION_PLAN"
    descriptions = {"missing": "Required field.", "extra_forbidden": "Unknown field.",
        "forbidden_parameter": "System-managed field.", "unsafe_input": "Paths and credentials are not accepted.",
        "string_type": "Must be a string.", "int_type": "Must be an integer.",
        "literal_error": "Unsupported choice.", "greater_than_equal": "Value is below the minimum.",
        "less_than_equal": "Value exceeds the maximum.", "json_invalid": "Malformed JSON."}
    details = []
    for item in errors[:30]:
        location = ".".join(str(p) for p in item["loc"] if p not in {"body", "query"})
        if item["type"] in {"forbidden_parameter", "unsafe_input"}:
            location = item.get("ctx", {}).get("field", location)
        details.append({"field": location or "request", "message": descriptions.get(item["type"], "Invalid type, value or constraint.")})
    return error_response(request, code, details)


def assistant_http_error(result: Any) -> str | None:
    if result.error_code in {"REQUEST_ID_CONFLICT", "REQUEST_ALREADY_SUBMITTED", "INVALID_SOURCE_SELECTION", "WORKSPACE_BUSY", "CHAT_BUSY"}:
        return result.error_code
    if result.status == "failed" and not result.artifacts and (result.error_code or "").startswith("LLM_"):
        return result.error_code
    return None
