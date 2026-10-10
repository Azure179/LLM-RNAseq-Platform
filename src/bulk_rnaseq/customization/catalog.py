"""Whitelist by capability; types and defaults come from existing Skill schemas."""

from typing import Any
from jsonschema import Draft202012Validator
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.tool_adapter import ToolAdapter
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.plot_options import color_hex, validate_display, validate_gene_labels
from .models import AnnotationFilterOptions, CustomizationDecision, EnrichmentPlotOptions

SKILLS = {"rnaseq.volcano", "rnaseq.heatmap", "rnaseq.deg", "rnaseq.go", "rnaseq.kegg"}


def parameter_schema(registry: SkillRegistry, target: str) -> dict[str, Any]:
    if target in {"rnaseq.go", "rnaseq.kegg"}:
        return EnrichmentPlotOptions.model_json_schema()
    if target == "rnaseq.deg":
        return {"type": "object", "properties": {}, "additionalProperties": False, "required": []}
    if target not in SKILLS:
        raise SkillError("UNSUPPORTED_CUSTOMIZATION", "This result type has no customization contract.", operation="customization")
    schema = ToolAdapter(registry).build_tool_definition(target)["function"]["parameters"]
    # group_column is bound from verified existing heatmap metadata, not changed here.
    schema["properties"].pop("group_column", None)
    schema["required"] = []
    return schema


def validate_decision(decision: CustomizationDecision, registry: SkillRegistry, target: str,
                      available_annotations: list[str]) -> CustomizationDecision:
    try:
        decision = CustomizationDecision.model_validate(decision.model_dump())
        if decision.target_skill != target or target not in SKILLS:
            raise ValueError("Target result cannot be changed by the model")
        if decision.mode == "unsupported":
            return decision
        if decision.mode == "annotation_filter":
            if target != "rnaseq.deg":
                raise ValueError("Filtering currently requires an existing DEG result")
            options = AnnotationFilterOptions.model_validate(decision.parameters)
            if not set(options.annotation_sources) <= set(available_annotations):
                raise ValueError("Requested annotations were not offered by the host")
        else:
            if target == "rnaseq.deg" or not decision.parameters:
                raise ValueError("No statistical DEG rerun in customization")
            schema = parameter_schema(registry, target)
            Draft202012Validator(schema).validate(decision.parameters)
            for key, value in decision.parameters.items():
                field = schema["properties"][key]
                kinds = field.get("type", [])
                if "integer" in ([kinds] if isinstance(kinds, str) else kinds) and value is not None and type(value) is not int:
                    raise ValueError("Expected integer, not coercion")
                if key.startswith("color_"):
                    color_hex(value)
                if key in {"gene_list", "label_genes"}:
                    validate_gene_labels(value)
                if key == "title":
                    validate_display(value, 1000, 1000)
        return decision
    except Exception as error:
        if isinstance(error, SkillError):
            raise
        raise SkillError("INVALID_CUSTOMIZATION_PARAMETERS", "Customization does not match its declared capability or whitelist.", operation="customization") from None
