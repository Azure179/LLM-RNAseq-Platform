"""Read-only, compact projection of Registry Manifests. Never retrieves executors."""

from importlib.resources import files
import json
from typing import Any

from bulk_rnaseq.registry import SkillRegistry


HOST_PARAMETERS = frozenset({"timeout", "rscript_executable"})


def build_catalog(registry: SkillRegistry) -> dict[str, Any]:
    definitions = json.loads(files("bulk_rnaseq.manifests").joinpath("schemas", "input-types.schema.json").read_text(encoding="utf-8"))["$defs"]

    def expand(value: Any) -> Any:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            prefix, name = value["$ref"].split("#/$defs/")
            if prefix not in ("", "schemas/input-types.schema.json"):
                raise ValueError("Unsupported input schema reference")
            resolved = {**expand(definitions[name]), **{key: item for key, item in value.items() if key != "$ref"}}
            if name == "path":
                resolved["x-path"] = True
            return expand(resolved)
        return {key: expand(item) for key, item in value.items()}

    catalog = {}
    for name in registry.list_skills():
        manifest = registry.get_manifest(name)
        schema = expand(manifest["input_schema"])
        schema["properties"] = {key: value for key, value in schema["properties"].items() if key not in HOST_PARAMETERS}
        catalog[name] = {"description": manifest["description"], "input_schema": schema}
    return catalog


def defaults_for(schema: dict[str, Any]) -> dict[str, Any]:
    return {key: value["default"] for key, value in schema["properties"].items()
            if "default" in value and value["default"] is not None}


def missing_for(schema: dict[str, Any], parameters: dict[str, Any]) -> list[str]:
    missing = [name for name in schema["required"] if parameters.get(name) is None]
    # Existing Heatmap and ORA schemas declare exactly-one alternatives in oneOf.
    alternatives = [branch["required"][0] for branch in schema.get("oneOf", [])]
    if alternatives and not any(parameters.get(name) is not None for name in alternatives):
        missing.append("|".join(alternatives))
    return missing


def is_path(schema: dict[str, Any]) -> bool:
    return schema.get("x-path", False) or any(is_path(branch) for branch in schema.get("anyOf", []))
