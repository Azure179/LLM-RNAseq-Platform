"""DeepSeek JSON -> Pydantic -> existing Manifest constraints. No Skill execution."""

from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from bulk_rnaseq.llm.client import ChatResponse, DeepSeekClient
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.intent_catalog import build_catalog, defaults_for, is_path, missing_for
from bulk_rnaseq.llm.intent_prompt import build_system_prompt
from bulk_rnaseq.llm.intent_schema import StructuredIntent
from bulk_rnaseq.llm.json_utils import unique_object as _object, reject_constant as _finite_constant
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.result_schema import json_copy


LOGGER = logging.getLogger(__name__)


def validate_parameters(parameters: dict[str, Any], schema: dict[str, Any]) -> None:
    # Partial request validation: missing required/oneOf alternatives are checked separately.
    partial = {"type": "object", "properties": schema["properties"], "additionalProperties": False}
    Draft202012Validator(partial).validate(parameters)
    for name, value in parameters.items():
        if schema["properties"][name].get("type") == "integer" and type(value) is not int:
            raise ValueError("Integer parameter requires an int")
    alternatives = [branch["required"][0] for branch in schema.get("oneOf", [])]
    if sum(parameters.get(name) is not None for name in alternatives) > 1:
        raise ValueError("Mutually exclusive parameters")


def parse_intent(response: ChatResponse, catalog: dict[str, Any], user_text: str,
                 context: dict[str, Any]) -> StructuredIntent:
    if response.finish_reason != "stop":
        raise DeepSeekError("LLM_INTENT_INCOMPLETE", "Intent response did not finish normally.")
    try:
        raw = json.loads(response.content, object_pairs_hook=_object, parse_constant=_finite_constant)
    except (ValueError, TypeError):
        raise DeepSeekError("LLM_INTENT_JSON_ERROR", "DeepSeek did not return a valid, unambiguous JSON object.") from None
    try:
        intent = StructuredIntent.model_validate(raw)
    except ValidationError:
        raise DeepSeekError("LLM_INTENT_SCHEMA_ERROR", "DeepSeek JSON does not match StructuredIntent.") from None
    try:
        if intent.status == "unsupported":
            return intent
        if intent.skill_name not in catalog:
            raise ValueError("Unregistered or disabled Skill")
        schema = catalog[intent.skill_name]["input_schema"]
        validate_parameters(intent.parameters, schema)
        missing = missing_for(schema, intent.parameters)
        if set(intent.missing_parameters) != set(missing):
            raise ValueError("Incorrect missing dependencies")
        if intent.status == "ready":
            Draft202012Validator(schema).validate(intent.parameters)
        defaults = defaults_for(schema)
        if not defaults.keys() <= intent.parameters.keys():
            raise ValueError("Project defaults must be represented")
        trusted = context.get(intent.skill_name, {})
        for quote in intent.evidence.values():
            if not quote.strip() or quote not in user_text:
                raise ValueError("Evidence must quote user text")
        for key, value in intent.parameters.items():
            if value is None:
                raise ValueError("Omit null parameters")
            if key in trusted and value == trusted[key]:
                continue
            if key in defaults and value == defaults[key]:
                continue
            quote = intent.evidence.get(key, "")
            if not quote.strip() or quote not in user_text:
                raise ValueError("Ungrounded parameter")
            if (key in {"treatment", "control", "group_column"} or is_path(schema["properties"][key])) and str(value) not in quote:
                raise ValueError("Invented path/group/column")
        if intent.parameters.get("treatment") is not None and intent.parameters.get("treatment") == intent.parameters.get("control"):
            raise ValueError("Treatment equals control")
        if set(intent.evidence) - (set(intent.parameters) | {"gene_subset"}):
            raise ValueError("Unexpected evidence key")
        if intent.gene_subset is not None:
            if intent.skill_name not in {"rnaseq.go", "rnaseq.kegg", "rnaseq.heatmap"}:
                raise ValueError("Subset unsupported for selected task")
            quote = intent.evidence.get("gene_subset", "")
            if not quote.strip() or quote not in user_text:
                raise ValueError("Ungrounded subset")
    except Exception:
        raise DeepSeekError("LLM_INTENT_VALIDATION_ERROR", "Intent violates the registered schema, dependency or grounding constraints.") from None
    return intent


@dataclass(frozen=True, slots=True)
class IntentResponse:
    intent: StructuredIntent
    response: ChatResponse
    prompt_sha256: str


class IntentParser:
    def __init__(self, client: DeepSeekClient, registry: SkillRegistry, *, max_tokens: int = 768) -> None:
        self._client = client
        self._catalog = build_catalog(registry)
        if type(max_tokens) is not int or not 1 <= max_tokens <= 1024:
            raise DeepSeekError("LLM_INPUT_ERROR", "Intent max_tokens must be in [1,1024].")
        self._max_tokens = max_tokens

    def parse(self, user_text: str, *, context: dict[str, Any] | None = None) -> IntentResponse:
        try:
            if context is not None and not isinstance(context, dict):
                raise ValueError("Context must be a mapping")
            trusted = json_copy({} if context is None else context)
            for name, parameters in trusted.items():
                validate_parameters(parameters, self._catalog[name]["input_schema"])
        except Exception:
            raise DeepSeekError("LLM_INPUT_ERROR", "Invalid trusted context; use registered parameter names only.") from None
        prompt = build_system_prompt(self._catalog, trusted)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        response = self._client.chat(user_text, system_prompt=prompt, json_mode=True, max_tokens=self._max_tokens)
        try:
            intent = parse_intent(response, self._catalog, user_text, trusted)
        except DeepSeekError as error:
            # Already checked by client for credential reflection. Never log raw text automatically.
            error.response = response
            error.prompt_sha256 = prompt_hash
            LOGGER.warning("DeepSeek intent rejected: code=%s", error.code)
            raise
        LOGGER.info("DeepSeek intent validated: status=%s skill=%s", intent.status, intent.skill_name)
        return IntentResponse(intent, response, prompt_hash)
