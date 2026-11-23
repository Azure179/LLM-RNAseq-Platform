"""HTTP error mapping and safe canonical SkillResult projection, never statistics."""

from dataclasses import fields
from pathlib import Path
from typing import Any, Literal, get_type_hints

from pydantic import ConfigDict, create_model

from bulk_rnaseq.result_schema import SCHEMA_VERSION, SkillResult
from bulk_rnaseq.workspace import ProjectManager
from bulk_rnaseq.workspace.skill_execution import SkillExecution
from bulk_rnaseq.api.errors import http_error
from bulk_rnaseq.api.security import safe_value


# Generate the HTTP schema from the existing contract, excluding its private legacy cache.
SkillResultResponse = create_model("SkillResultResponse", __config__=ConfigDict(extra="forbid"),
    schema_version=(Literal[SCHEMA_VERSION], SCHEMA_VERSION),
    **{field.name: (get_type_hints(SkillResult)[field.name], ...) for field in fields(SkillResult)
       if not field.name.startswith("_")})



def public_result(execution: SkillExecution, manager: ProjectManager) -> dict[str, Any]:
    result = SkillResult.from_dict(execution.result.to_dict())
    project = manager.get_project_context(execution.project_id)
    outputs = {}
    for name, value in result.outputs.items():
        path = manager._safe_path(Path(value))
        if not path.is_relative_to(project.output_root) or not path.is_file():
            raise ValueError("Invalid output reference")
        outputs[name] = path.relative_to(project.project_root).as_posix()
    record = execution.record
    sources = {key: {field: value for field, value in source.items() if field in
               {"source", "key", "run_id", "source_run_id", "source_skill", "output", "gene_subset", "sha256", "selected_genes", "derived_sha256", "reuse_scope"}}
               for key, source in record.get("upstream_sources", {}).items()}
    parent_ids = sorted({source["source_run_id"] for source in sources.values() if "source_run_id" in source})
    metadata = {"project_id": execution.project_id, "workflow_id": execution.workflow_id, "step_id": record["step_id"],
        "created_at": record["started_at"], "completed_at": record["finished_at"],
        "source_run_id": parent_ids[0] if len(parent_ids) == 1 else None,
        "parent_run_ids": parent_ids, "upstream_sources": sources,
        "parameters": record["parameters"], "sample_selection": record.get("sample_selection", {})}
    payload = result.to_dict()
    payload.update(inputs={"parameters": safe_value(record["parameters"])}, outputs=outputs,
                   metadata=safe_value(metadata), summary=safe_value(result.summary),
                   warnings=safe_value(result.warnings), details={})
    if result.status == "failed":
        payload["message"] = "Skill execution did not complete. See error_code and the local run record."
        # Unknown backend exception codes/text are not a public error contract.
        payload["error_code"] = http_error(result.error_code)[1]
    SkillResult.from_dict(payload)
    return payload
