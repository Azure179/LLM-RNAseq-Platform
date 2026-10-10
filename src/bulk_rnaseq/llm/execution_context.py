"""Explicit host-owned file bindings; never inferred from model text."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.intent import validate_parameters
from bulk_rnaseq.llm.intent_catalog import build_catalog, is_path
from bulk_rnaseq.llm.tool_adapter import ToolAdapter
from bulk_rnaseq.registry import SkillRegistry


class ExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_root: Path
    output_root: Path
    bindings: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    confirmed_parameters: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    rscript_executable: str = "Rscript"
    timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    def local_path(self, value: str | Path) -> Path:
        root = self.project_root.resolve()
        path = (root / value).resolve()
        if not path.is_relative_to(root) or any(part == ".git" or part.startswith(".env") for part in path.relative_to(root).parts):
            raise DeepSeekError("CONTEXT_PATH_NOT_ALLOWED", "Context path must stay inside the project and must not name secret files.")
        return path

    def check(self, registry: SkillRegistry, adapter: ToolAdapter) -> None:
        """Fail before a paid request on malformed trusted configuration."""
        if not self.project_root.is_dir():
            raise DeepSeekError("INVALID_EXECUTION_CONTEXT", "Project root must exist.")
        self.local_path(self.output_root)
        catalog = build_catalog(registry)
        for name, bindings in self.bindings.items():
            managed = set(adapter.system_managed_parameters(name)) - {"output_dir", "timeout", "rscript_executable"}
            if not bindings.keys() <= managed:
                raise DeepSeekError("INVALID_EXECUTION_CONTEXT", "Bindings accept input files only, using exact Skill parameter names.")
            validate_parameters(bindings, catalog[name]["input_schema"])
            for key, value in bindings.items():
                if value is not None and is_path(catalog[name]["input_schema"]["properties"][key]):
                    self.local_path(value)
        for name, values in self.confirmed_parameters.items():
            validate_parameters(values, adapter.build_tool_definition(name)["function"]["parameters"])

    def inject(self, name: str, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        inputs = dict(parameters)
        for key, value in self.bindings.get(name, {}).items():
            if value is not None:
                path = self.local_path(value)
                if not path.is_file():
                    raise DeepSeekError("CONTEXT_FILE_NOT_FOUND", "A bound input file does not exist.", details={"parameter": key})
                inputs[key] = str(path)
            else:
                inputs[key] = None
        inputs["output_dir"] = str(self.local_path(output_dir))
        inputs["rscript_executable"] = self.rscript_executable
        if self.timeout_seconds is not None:
            inputs["timeout"] = self.timeout_seconds
        return inputs

    def public_context(self) -> dict[str, Any]:
        # Send parameter values and availability NAMES, never local paths/executable settings.
        return {"confirmed_parameters": self.confirmed_parameters,
                "host_bound_fields": {name: [key for key, value in fields.items() if value is not None]
                                      for name, fields in self.bindings.items()}}
