"""Small in-process registry for explicitly trusted executors; never imports manifest code."""

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.resources import files
import inspect
import json
import logging
from pathlib import Path
import re
from typing import Any, get_args, get_type_hints

from jsonschema import Draft202012Validator
from referencing import Registry as SchemaReferences, Resource

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.models import ResultEnvelope
from bulk_rnaseq.result_schema import SkillResult, json_copy


Executor = Callable[..., ResultEnvelope | SkillResult]
LOGGER = logging.getLogger(__name__)
# Identifier namespace only. All referenced documents are local package resources.
SCHEMA_BASE = "https://bulk-rnaseq.invalid/manifests/"


def _error(code: str, message: str, name: str = "rnaseq.registry", **details: Any) -> SkillError:
    operation = name.removeprefix("rnaseq.") if isinstance(name, str) and re.fullmatch(r"rnaseq\.[a-z][a-z0-9_]*", name) else "registry"
    return SkillError(code, message, operation=operation, details=details)


@dataclass(frozen=True, slots=True)
class RegisteredSkill:
    name: str
    executor: Executor | None
    _manifest: dict[str, Any] = field(repr=False)
    _input_validator: Draft202012Validator = field(repr=False)
    _output_validator: Draft202012Validator = field(repr=False)
    _path_parameters: frozenset[str] = field(default_factory=frozenset, repr=False)

    @property
    def manifest(self) -> dict[str, Any]:
        return json_copy(self._manifest)

    @property
    def input_schema(self) -> dict[str, Any] | bool:
        return json_copy(self._manifest["input_schema"])

    @property
    def available(self) -> bool:
        return self.executor is not None


class SkillRegistry:
    """Register at startup, then query/execute synchronously; no global singleton."""

    def __init__(self) -> None:
        self._skills: dict[str, RegisteredSkill] = {}
        resources = files("bulk_rnaseq.manifests").joinpath("schemas")
        try:
            documents = [(SCHEMA_BASE + "schemas/" + name,
                          Resource.from_contents(json.loads(resources.joinpath(name).read_text(encoding="utf-8"))))
                         for name in ("input-types.schema.json", "skill-result.schema.json")]
            self._references = SchemaReferences().with_resources(documents)
        except (OSError, ValueError) as cause:
            raise _error("REGISTRY_CONFIG_ERROR", "Unable to load bundled JSON Schemas.") from cause

    def _validator(self, schema: dict[str, Any] | bool, name: str) -> Draft202012Validator:
        Draft202012Validator.check_schema(schema)
        uri = SCHEMA_BASE + name + ".json"
        rooted = {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema, "$id": uri} if isinstance(schema, dict) else schema
        resolver = (self._references.with_resource(uri, Resource.from_contents(rooted)).resolver(uri)
                    if isinstance(rooted, dict) else None)
        # Only bundled external references are supported. No remote retrieval or caller file reads.
        def check_refs(value: Any) -> None:
            if isinstance(value, dict):
                if any(key in value for key in ("$id", "$dynamicRef", "$recursiveRef")):
                    raise ValueError("Custom schema identifiers/dynamic references are unsupported.")
                if "$ref" in value:
                    ref = value["$ref"]
                    if not ref.startswith("#"):
                        if ref.split("#", 1)[0] not in {"schemas/input-types.schema.json", "schemas/skill-result.schema.json"}:
                            raise ValueError("Only bundled schema references are supported.")
                    resolver.lookup(ref)
                for child in value.values():
                    check_refs(child)
            elif isinstance(value, list):
                for child in value:
                    check_refs(child)
        check_refs(schema)
        return Draft202012Validator(rooted, registry=self._references)

    def register(self, manifest: dict[str, Any], executor: Executor | None = None) -> None:
        """Snapshot a trusted description + callable; duplicates never replace existing entries."""
        name = manifest.get("name") if isinstance(manifest, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r"rnaseq\.[a-z][a-z0-9_]*", name):
            raise _error("INVALID_MANIFEST", "Expected a rnaseq.* Skill name.")
        if name in self._skills:
            raise _error("DUPLICATE_SKILL", "Skill is already registered.", name, registered_name=name)
        try:
            data = json_copy(manifest)
            required = {"version", "description", "category", "input_schema", "output_schema", "requirements", "dependencies", "limitations"}
            if not required <= data.keys() or data.get("manifest_schema_version") != "1.0":
                raise ValueError("Missing manifest fields or unsupported layout version.")
            enabled = data.get("availability") == {"status": "implemented", "callable": True}
            disabled = data.get("availability") == {"status": "not_implemented", "callable": False}
            if not (enabled or disabled) or enabled != callable(executor) or (disabled and executor is not None):
                raise ValueError("Availability must agree with the supplied executor.")
            if disabled and (data["input_schema"] is not False or data["output_schema"] is not False):
                raise ValueError("Unimplemented skills must reject both input and output.")
            paths: frozenset[str] = frozenset()
            if enabled:
                signature = inspect.signature(executor)
                schema = data["input_schema"]
                if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                    raise ValueError("Input schema must be a closed parameter object.")
                if set(schema["properties"]) != set(signature.parameters):
                    raise ValueError("Manifest parameters differ from executor signature.")
                needed = {key for key, p in signature.parameters.items() if p.default is inspect.Parameter.empty}
                if set(schema.get("required", [])) != needed:
                    raise ValueError("Required inputs differ from executor signature.")
                if any(p.kind not in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) for p in signature.parameters.values()):
                    raise ValueError("Executors must accept named parameters without variadic arguments.")
                hints = get_type_hints(executor)
                paths = frozenset(key for key in signature.parameters if hints.get(key) is Path or Path in get_args(hints.get(key)))
            entry = RegisteredSkill(name, executor, data, self._validator(data["input_schema"], name),
                                    self._validator(data["output_schema"], name), paths)
        except Exception as cause:
            raise _error("INVALID_MANIFEST", "Manifest or executor contract is invalid.", name,
                         reason_type=type(cause).__name__) from cause
        self._skills[name] = entry
        LOGGER.info("Skill registered: name=%s available=%s", name, entry.available)

    def get(self, name: str) -> RegisteredSkill:
        if not isinstance(name, str) or name not in self._skills:
            raise _error("UNKNOWN_SKILL", "Skill is not registered.", name)
        return self._skills[name]

    def get_manifest(self, name: str) -> dict[str, Any]:
        return self.get(name).manifest

    def get_input_schema(self, name: str) -> dict[str, Any] | bool:
        return self.get(name).input_schema

    def get_executor(self, name: str) -> Executor:
        executor = self.get(name).executor
        if executor is None:
            raise _error("SKILL_UNAVAILABLE", "Skill is described but not implemented.", name)
        return executor

    def list_skills(self, *, available_only: bool = True) -> list[str]:
        return sorted(name for name, skill in self._skills.items() if skill.available or not available_only)

    def execute(self, name: str, validated_input: dict[str, Any]) -> SkillResult:
        """Return a canonical result on success OR failure; never trust the caller's validation label."""
        try:
            entry = self.get(name)
            executor = self.get_executor(name)
            arguments = self.validate_input(name, validated_input)
            LOGGER.info("Registry execution started: skill=%s", name)
            returned = executor(**arguments)
            try:
                result = returned.to_skill_result() if isinstance(returned, ResultEnvelope) else returned
                if not isinstance(result, SkillResult) or result.skill_name != name:
                    raise ValueError("Unexpected result type or Skill identity")
                result = SkillResult.from_dict(result.to_dict())  # Snapshot and revalidate mutable nested data.
                entry._output_validator.validate(result.to_dict())
            except Exception as cause:
                raise _error("INVALID_SKILL_RESULT", "Executor returned an incompatible result.", name) from cause
            LOGGER.info("Registry execution finished: skill=%s status=%s run_id=%s", name, result.status, result.run_id)
            return result
        except SkillError as error:
            LOGGER.error("Registry execution failed: skill=%s code=%s", name, error.code)
            try:
                if error.to_skill_result().skill_name != name:
                    error.operation = _error(error.code, str(error), name).operation
                return error.to_skill_result()
            except Exception:
                return _error("INTERNAL_ERROR", "Executor failure details could not be serialized.", name).to_skill_result()
        except Exception as cause:
            # Existing Skills write internal tracebacks to run.log. Do not expose plugin exception text.
            LOGGER.error("Unexpected registry executor failure: skill=%s type=%s", name, type(cause).__name__)
            return _error("INTERNAL_ERROR", "Unexpected execution failure; inspect host logs.", name).to_skill_result()

    def validate_input(self, name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Validate without execution; return a detached, Path-converted input."""
        entry = self.get(name)
        try:
            if not isinstance(inputs, dict):
                raise ValueError("Expected an object")
            arguments = json_copy(inputs)
        except (TypeError, ValueError, OverflowError) as cause:
            raise _error("INVALID_INPUT", "Inputs must be a JSON-compatible parameter object.", name) from cause
        problem = next(entry._input_validator.iter_errors(arguments), None)
        if problem is not None:
            raise _error("INVALID_INPUT", "Inputs do not match the Skill input schema.", name,
                         field_path=list(problem.absolute_path), rule=problem.validator)
        for key in entry._path_parameters:
            if isinstance(arguments.get(key), str):
                arguments[key] = Path(arguments[key])
        return arguments
