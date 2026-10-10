"""Read dependency metadata from existing Manifests; never duplicate Skill contracts."""

from typing import Any

from pydantic import Field

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.intent_catalog import build_catalog, is_path
from bulk_rnaseq.registry import SkillRegistry
from .models import Identifier, Model


class WorkflowMetadata(Model):
    project_inputs: dict[str, Identifier]
    subset_source_outputs: list[Identifier] = Field(default_factory=list)
    subset_target_inputs: list[Identifier] = Field(default_factory=list)


class DependencyContracts:
    def __init__(self, registry: SkillRegistry) -> None:
        self.schemas: dict[str, dict[str, Any]] = {}
        self.outputs: dict[str, set[str]] = {}
        self.metadata: dict[str, WorkflowMetadata] = {}
        # (source Skill, actual output key, target Skill, actual input parameter)
        self.edges: set[tuple[str, str, str, str]] = set()
        try:
            catalog = build_catalog(registry)
            for name, item in catalog.items():
                manifest = registry.get_manifest(name)
                self.schemas[name] = item["input_schema"]
                self.outputs[name] = set(manifest["output_schema"]["then"]["properties"]["outputs"]["properties"])
                meta = WorkflowMetadata.model_validate(manifest["dependencies"]["workflow"])
                data_inputs = {key for key, schema in item["input_schema"]["properties"].items()
                               if is_path(schema) and key != "output_dir"}
                if set(meta.project_inputs) != data_inputs:
                    raise ValueError("Workflow input roles must cover exactly the data parameters")
                if not set(meta.subset_source_outputs) <= self.outputs[name]:
                    raise ValueError("Subset source is not an actual output")
                if not set(meta.subset_target_inputs) <= data_inputs:
                    raise ValueError("Subset target is not a data input")
                self.metadata[name] = meta
                for edge in manifest["dependencies"]["upstream"]:
                    self.edges.add((edge["skill"], edge["output"], name, edge["input"]))
                for edge in manifest["dependencies"]["downstream"]:
                    self.edges.add((name, edge["output"], edge["skill"], edge["input"]))
            for source, output, target, parameter in self.edges:
                if (output not in self.outputs[source]
                        or parameter not in self.metadata[target].project_inputs):
                    raise ValueError("Manifest dependency refers to an undefined artifact/input")
        except (KeyError, TypeError, ValueError) as cause:
            raise SkillError("INVALID_WORKFLOW_CONTRACT", "Workflow metadata differs from registered Skill contracts.",
                             operation="workflow", details={"reason_type": type(cause).__name__}) from cause

    def has_upstream(self, skill: str, parameter: str) -> bool:
        return any(target == skill and field == parameter for _, _, target, field in self.edges)
