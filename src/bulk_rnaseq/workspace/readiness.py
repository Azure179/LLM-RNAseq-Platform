"""Manifest-derived dependencies and a host-owned Phase 6 context bridge.

No executor lookup, network request, output reservation or analysis execution.
"""

from pathlib import Path
from typing import Any, TYPE_CHECKING

from jsonschema.exceptions import ValidationError

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.execution_context import ExecutionContext
from bulk_rnaseq.llm.intent_catalog import build_catalog, defaults_for, is_path, missing_for
from bulk_rnaseq.llm.tool_adapter import ToolAdapter
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.validation import validate_inputs, validate_parameters, validate_pca_inputs
from bulk_rnaseq.workspace.errors import ProjectError
from bulk_rnaseq.workspace.file_models import FileRole

if TYPE_CHECKING:
    from bulk_rnaseq.workspace.file_import import FileImportService


# Only bridge existing executor parameter names to canonical file roles.
# Requiredness, defaults, constraints and alternatives stay in the Manifest.
PARAMETER_ROLES = {
    "count_matrix_path": FileRole.COUNT_MATRIX, "count_matrix": FileRole.COUNT_MATRIX,
    "raw_counts": FileRole.COUNT_MATRIX, "metadata_path": FileRole.METADATA,
    "metadata": FileRole.METADATA, "gene_to_go_mapping": FileRole.GO_MAPPING,
    "gene_to_pathway_mapping": FileRole.KEGG_MAPPING,
}


def _deg_check(inputs: dict[str, Any]) -> None:
    validate_parameters(inputs["treatment"], inputs["control"], inputs["alpha"], inputs["lfc_threshold"], 1)
    validate_inputs(Path(inputs["count_matrix_path"]), Path(inputs["metadata_path"]), inputs["treatment"], inputs["control"])


def _pca_check(inputs: dict[str, Any]) -> None:
    validate_pca_inputs(Path(inputs["count_matrix"]), Path(inputs["metadata"]), inputs["group_column"])


PREFLIGHT_CHECKS = {"rnaseq.deg": _deg_check, "rnaseq.pca": _pca_check}


def _prepare(service: "FileImportService", project_key: str, registry: SkillRegistry,
             confirmed_parameters: dict[str, dict[str, Any]] | None) -> tuple[ExecutionContext, dict, dict]:
    project = service.manager.open_project(project_key)
    records, errors = {}, {}
    for role in FileRole:
        try:
            record = service._record(project, role)
            if record is not None:
                records[role] = record
        except ProjectError as error:
            errors[role] = {"error_code": error.code, "message": str(error)}
    catalog = build_catalog(registry)
    bindings, confirmed = {}, {}
    for name, entry in catalog.items():
        properties = entry["input_schema"]["properties"]
        bindings[name], confirmed[name] = {}, {}
        for key in properties:
            role = PARAMETER_ROLES.get(key)
            if role in records:
                bindings[name][key] = records[role]["stored_path"]
                if role == FileRole.METADATA and "group_column" in properties:
                    confirmed[name]["group_column"] = records[role]["summary"]["group_column"]
                if role in {FileRole.GO_MAPPING, FileRole.KEGG_MAPPING} and records[role]["mapping_sheet"] is not None:
                    confirmed[name]["mapping_sheet"] = records[role]["mapping_sheet"]
    try:
        for name, parameters in (confirmed_parameters or {}).items():
            if name not in confirmed or not isinstance(parameters, dict):
                raise ValueError("Unknown Skill or invalid parameter object")
            confirmed[name].update(parameters)
        context = ExecutionContext(project_root=project.root, output_root=project.root / "results",
                                   bindings=bindings, confirmed_parameters=confirmed)
        context.check(registry, ToolAdapter(registry))
    except (DeepSeekError, ValidationError, ValueError, TypeError, AttributeError):
        raise ProjectError("INVALID_PROJECT_PARAMETERS", "Use registered business parameters only; file paths are injected by the import service.") from None
    return context, catalog, errors


def build_execution_context(service: "FileImportService", project_key: str, *,
                            registry: SkillRegistry | None = None,
                            confirmed_parameters: dict[str, dict[str, Any]] | None = None) -> ExecutionContext:
    """Bind verified local copies only. Rebuild immediately before a future request.

    Missing/changed files are not exposed to the router; readiness reports them.
    This is a context constructor, not permission to run a Skill.
    """
    return _prepare(service, project_key, registry or create_default_registry(), confirmed_parameters)[0]


def get_project_readiness(service: "FileImportService", project_key: str, *,
                          registry: SkillRegistry | None = None,
                          confirmed_parameters: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    registry = registry or create_default_registry()
    context, catalog, file_errors = _prepare(service, project_key, registry, confirmed_parameters)
    readiness = {}
    for name, entry in catalog.items():
        schema = entry["input_schema"]
        inputs = {**defaults_for(schema), **context.confirmed_parameters[name],
                  **{key: str(context.local_path(value)) for key, value in context.bindings[name].items()},
                  "output_dir": str(context.output_root)}
        missing = missing_for(schema, inputs)
        missing_data = [item for item in missing if any(is_path(schema["properties"][key]) for key in item.split("|"))]
        errors = [dict(error, role=role.value) for role, error in file_errors.items()
                  if any(PARAMETER_ROLES.get(key) == role for key in schema["properties"])]
        if not missing and not errors:
            try:
                registry.validate_input(name, inputs)
                check = PREFLIGHT_CHECKS.get(name)
                if check:
                    check(inputs)
            except SkillError as error:
                errors.append({"error_code": error.code, "message": str(error), "details": error.details})
        readiness[name] = {"ready": not missing and not errors, "data_ready": not missing_data and not errors,
                           "missing": missing, "missing_data": missing_data, "errors": errors,
                           "scope": "input readiness only; R packages, write access at run time and statistical fitting are not executed"}
    return readiness
