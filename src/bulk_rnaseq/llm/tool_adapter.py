"""Read-only Manifest projection into ordinary DeepSeek function definitions.

No file binding, argument dispatch, or executor access. Parameter roles are a
security policy; types, bounds, descriptions and defaults remain in Manifests.
"""

import json
import logging
import re
from typing import Any

from jsonschema import Draft202012Validator

from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.intent_catalog import HOST_PARAMETERS, build_catalog, is_path
from bulk_rnaseq.registry import SkillRegistry


LOGGER = logging.getLogger(__name__)
TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
# Reviewed scalar roles, NOT another parameter schema. Unknown future scalars
# fail closed until explicitly reviewed; filesystem inputs are always hidden.
LLM_PARAMETERS = frozenset({
    "treatment", "control", "alpha", "lfc_threshold", "group_column",
    "padj_threshold", "log2fc_threshold", "top_n", "top_n_labels",
    "pvalue_threshold", "ontology", "mapping_sheet",
    "label_genes", "color_up", "color_down", "color_ns", "title", "figure_width", "figure_height",
    "gene_list", "ranking_method", "show_gene_names",
})


def validate_tool_definitions(definitions: list[dict[str, Any]]) -> None:
    """Validate this adapter's non-strict function profile before any network IO."""
    try:
        json.dumps(definitions, allow_nan=False)
        if not isinstance(definitions, list) or not 1 <= len(definitions) <= 128:
            raise ValueError("Expected 1..128 tools")
        names = set()
        for tool in definitions:
            if set(tool) != {"type", "function"} or tool["type"] != "function":
                raise ValueError("Only function tools are supported")
            function = tool["function"]
            if set(function) != {"name", "description", "parameters"}:
                raise ValueError("Unexpected function fields")
            name = function["name"]
            if not isinstance(name, str) or not TOOL_NAME.fullmatch(name) or name in names:
                raise ValueError("Invalid or duplicate function name")
            names.add(name)
            if not isinstance(function["description"], str) or not function["description"].strip():
                raise ValueError("Empty description")
            schema = function["parameters"]
            Draft202012Validator.check_schema(schema)
            if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                raise ValueError("Parameters must be a closed object")
            required = schema["required"]
            if len(set(required)) != len(required) or not set(required) <= schema["properties"].keys():
                raise ValueError("Invalid required fields")
            for field in schema["properties"].values():
                if "default" in field:
                    Draft202012Validator(field).validate(field["default"])
    except Exception:
        raise DeepSeekError("LLM_TOOL_SCHEMA_ERROR", "Invalid tool definition, name, required field or default.") from None


def _clean(value: Any) -> Any:
    """Drop local reference-resolution annotations, not validation constraints."""
    if isinstance(value, dict):
        return {key: _clean(child) for key, child in value.items() if key not in {"$schema", "x-path"}}
    if isinstance(value, list):
        return [_clean(child) for child in value]
    return value


class ToolAdapter:
    def __init__(self, registry: SkillRegistry) -> None:
        self._definitions: dict[str, dict[str, Any]] = {}
        self._managed: dict[str, list[str]] = {}
        self._skill_to_tool: dict[str, str] = {}
        self._tool_to_skill: dict[str, str] = {}
        try:
            catalog = build_catalog(registry)  # Same reference expansion as Phase 6.1.
            for skill_name, entry in catalog.items():
                tool_name = skill_name.replace(".", "_")  # The ONLY name conversion site.
                if not TOOL_NAME.fullmatch(tool_name) or tool_name in self._tool_to_skill:
                    raise ValueError("Tool name collision or unsupported name")
                manifest = registry.get_manifest(skill_name)
                source = entry["input_schema"]
                properties = {}
                managed = set(manifest["input_schema"]["properties"]) & HOST_PARAMETERS
                for name, field in source["properties"].items():
                    if is_path(field):
                        managed.add(name)
                    elif name in LLM_PARAMETERS:
                        properties[name] = _clean(field)
                    else:
                        raise DeepSeekError("LLM_TOOL_POLICY_ERROR", "Unreviewed parameter role; do not expose automatically.",
                                            details={"skill_name": skill_name, "parameter": name})
                # Existing oneOf constraints concern system-bound data sources only.
                # Never silently drop a future constraint involving model parameters.
                allowed = {"$schema", "type", "properties", "required", "additionalProperties", "oneOf"}
                if set(source) - allowed:
                    raise ValueError("Unsupported object-level constraint")
                for branch in source.get("oneOf", []):
                    referenced = set(branch.get("required", [])) | set(branch.get("properties", {}))
                    if not referenced <= managed or set(branch) - {"required", "properties"}:
                        raise ValueError("Cannot project mixed host/model oneOf")
                parameters = {"type": "object", "properties": properties,
                              "required": [name for name in source["required"] if name in properties],
                              "additionalProperties": False}
                description = "\n".join([
                    manifest["description"],
                    "Use when: " + " ".join(manifest["use_when"]),
                    "Do not use when: " + " ".join(manifest["do_not_use_when"]),
                    "Raw count requirement: " + manifest["raw_count_requirement"],
                    "Required host-bound inputs: " + ", ".join(name for name in source["required"] if name in managed) + ".",
                    "Host input alternatives: " + "; ".join(
                        " + ".join(branch["required"]) for branch in source.get("oneOf", [])) + ".",
                    "Upstream: " + " ".join(edge["skill"] + ": " + edge["notes"] for edge in manifest["dependencies"]["upstream"]),
                    "Host must bind and validate data/output paths before execution. Never supply paths or commands as arguments.",
                ])
                self._definitions[skill_name] = {"type": "function", "function": {
                    "name": tool_name, "description": description, "parameters": parameters}}
                self._managed[skill_name] = sorted(managed)
                self._skill_to_tool[skill_name] = tool_name
                self._tool_to_skill[tool_name] = skill_name
            validate_tool_definitions(list(self._definitions.values()))
        except DeepSeekError:
            raise
        except Exception:
            raise DeepSeekError("LLM_TOOL_SCHEMA_ERROR", "Unable to project the registered Skill schemas safely.") from None
        LOGGER.info("Tool schemas built: count=%d; executor access=none", len(self._definitions))

    def skill_name_to_tool_name(self, skill_name: str) -> str:
        if not isinstance(skill_name, str) or skill_name not in self._skill_to_tool:
            raise DeepSeekError("LLM_TOOL_UNKNOWN_SKILL", "Unknown or unavailable Skill.")
        return self._skill_to_tool[skill_name]

    def tool_name_to_skill_name(self, tool_name: str) -> str:
        if not isinstance(tool_name, str) or tool_name not in self._tool_to_skill:
            raise DeepSeekError("LLM_TOOL_UNKNOWN_NAME", "Unknown tool name.")
        return self._tool_to_skill[tool_name]

    def build_tool_definition(self, skill_name: str) -> dict[str, Any]:
        self.skill_name_to_tool_name(skill_name)
        return json.loads(json.dumps(self._definitions[skill_name], allow_nan=False))

    def build_all_tool_definitions(self) -> list[dict[str, Any]]:
        return [self.build_tool_definition(name) for name in self._definitions]

    def system_managed_parameters(self, skill_name: str) -> list[str]:
        self.skill_name_to_tool_name(skill_name)
        return list(self._managed[skill_name])
