"""One real DeepSeek JSON request -> existing WorkflowPlan -> local graph check.

No execution, automatic repair, output reservation, or biological computation.
"""

from dataclasses import dataclass, replace
import hashlib
import json
import logging
import re
from typing import Any, Literal, Self

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import Field, ValidationError, model_validator

from bulk_rnaseq.llm.client import ChatResponse, DeepSeekClient
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.json_utils import unique_object, reject_constant
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.result_schema import json_copy
from bulk_rnaseq.workspace.models import validate_name
from .context import WorkflowContext
from .models import Model, PlanValidationResult, ProjectResultBinding, WorkflowPlan, WorkflowStep
from .planner_prompt import build_planner_catalog, build_planner_prompt
from .validator import PlanValidator

LOGGER = logging.getLogger(__name__)
# Security screening only, never a natural-language task/contrast parser.
LOCAL_PATH = re.compile(r"[A-Za-z]:[\\/]|\\\\|(?:^|[\s\"'=])/[^\s]")


class PlannerDecision(Model):
    """Response envelope; steps reuse Phase 7.1, no replacement WorkflowPlan schema."""

    status: Literal["ready", "needs_input", "unsupported"]
    steps: list[WorkflowStep] = Field(default_factory=list, max_length=8)
    missing_parameters: list[str] = Field(default_factory=list)
    message: str = Field(default="", max_length=1500)

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        if self.status == "ready":
            if not self.steps or self.missing_parameters:
                raise ValueError("Ready requires steps and no missing parameters")
        elif self.steps or not self.message.strip():
            raise ValueError("Non-ready responses must clarify without partial steps")
        if self.status == "needs_input" and not self.missing_parameters:
            raise ValueError("needs_input requires missing parameters")
        if self.status == "unsupported" and self.missing_parameters:
            raise ValueError("Unsupported cannot request execution parameters")
        if any(not key.strip() for key in self.missing_parameters):
            raise ValueError("Missing parameter names must be non-empty")
        return self


@dataclass(frozen=True)
class PlannerResponse:
    decision: PlannerDecision
    plan: WorkflowPlan | None
    validation: PlanValidationResult | None
    response: ChatResponse
    prompt_sha256: str


def _safe_strings(value: Any) -> bool:
    if isinstance(value, str):
        return LOCAL_PATH.search(value) is None
    if isinstance(value, dict):
        return all(_safe_strings(key) and _safe_strings(child) for key, child in value.items())
    if isinstance(value, list):
        return all(_safe_strings(child) for child in value)
    return True


class WorkflowPlanner:
    def __init__(self, client: DeepSeekClient, registry: SkillRegistry) -> None:
        self.client = client
        self.validator = PlanValidator(registry)
        self.resolver = self.validator.resolver  # Compatibility for read-only catalog access.
        self.catalog = build_planner_catalog(registry, self.resolver.contracts)

    def _context(self, context: WorkflowContext, available_groups: list[str]) -> tuple[WorkflowContext, dict[str, Any]]:
        try:
            validate_name(context.project_id)
            confirmed = json_copy(context.confirmed_parameters)
            for name, parameters in confirmed.items():
                schema = {**self.catalog[name]["parameters"], "required": []}
                Draft202012Validator(schema).validate(parameters)
            roles = {role for item in self.catalog.values() for role in item["project_input_roles"].values()}
            if not context.available_inputs <= roles:
                raise ValueError("Unrecognized data role")
            if (not isinstance(available_groups, list) or len(available_groups) > 100
                    or any(not isinstance(group, str) or not group.strip() or len(group) > 160 for group in available_groups)):
                raise ValueError("Invalid available group names")
            for parameters in confirmed.values():
                if available_groups and any(parameters.get(key) is not None and parameters[key] not in available_groups
                                            for key in ("treatment", "control")):
                    raise ValueError("Confirmed comparison contradicts group inventory")
                if parameters.get("treatment") is not None and parameters.get("treatment") == parameters.get("control"):
                    raise ValueError("Confirmed comparison requires different groups")
            public = {"AVAILABLE_PROJECT_INPUTS": sorted(context.available_inputs),
                      "AVAILABLE_GROUPS": available_groups, "CONFIRMED_PARAMETERS": confirmed}
            if not _safe_strings(public) or self.client.sanitize_for_audit(public) != public:
                raise ValueError("Unsafe context")
            # Snapshot host state; do not expose project name, paths or history provider to LLM.
            snapshot = replace(context, available_inputs=frozenset(context.available_inputs),
                               confirmed_parameters=confirmed, history=None)
            return snapshot, public
        except (KeyError, TypeError, ValueError, AttributeError, SchemaValidationError):
            # Do not expose JSON-schema error text (it can include private parameter values).
            raise DeepSeekError("LLM_PLANNER_INPUT_ERROR", "Use only registered business parameters and logical project input roles.") from None

    def plan(self, user_text: str, *, context: WorkflowContext,
             available_groups: list[str] | None = None) -> PlannerResponse:
        if not isinstance(user_text, str) or not user_text.strip() or not _safe_strings(user_text):
            raise DeepSeekError("LLM_PLANNER_INPUT_ERROR", "Provide task text without local filesystem paths.")
        snapshot, public_context = self._context(context, [] if available_groups is None else available_groups)
        prompt = build_planner_prompt(self.catalog, self.resolver.contracts,
                                      PlannerDecision.model_json_schema(), public_context)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        response = self.client.chat(user_text, system_prompt=prompt, json_mode=True, max_tokens=1024)
        try:
            result = self._parse(response, user_text, snapshot, public_context["AVAILABLE_GROUPS"], prompt_hash)
        except DeepSeekError as error:
            error.response, error.prompt_sha256 = response, prompt_hash
            LOGGER.warning("Planner rejected: response_id=%s code=%s", response.response_id, error.code)
            raise
        LOGGER.info("Planner response: response_id=%s status=%s steps=%d model=%s usage=%s latency_seconds=%.3f",
                    response.response_id, result.decision.status, len(result.decision.steps),
                    response.model, response.usage, response.latency_seconds)
        return result

    def _parse(self, response: ChatResponse, user_text: str, context: WorkflowContext,
               groups: list[str], prompt_hash: str) -> PlannerResponse:
        if response.finish_reason != "stop":
            raise DeepSeekError("LLM_PLANNER_INCOMPLETE", "Planner response was incomplete; no repair or execution attempted.")
        try:
            raw = json.loads(response.content, object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (TypeError, ValueError):
            raise DeepSeekError("LLM_PLANNER_JSON_ERROR", "Planner returned invalid or ambiguous JSON.") from None
        try:
            decision = PlannerDecision.model_validate(raw)
        except ValidationError:
            raise DeepSeekError("LLM_PLANNER_SCHEMA_ERROR", "Planner JSON does not match the existing step schema and response envelope.") from None
        if not _safe_strings(raw):
            raise DeepSeekError("LLM_PLANNER_UNSAFE_RESPONSE", "Planner must not generate local paths.")
        if decision.status != "ready":
            return PlannerResponse(decision, None, None, response, prompt_hash)
        for step in decision.steps:
            if any(isinstance(binding, ProjectResultBinding) for binding in step.input_bindings.values()):
                raise DeepSeekError("LLM_PLANNER_BINDING_ERROR", "No historical runs were offered to this planner.")
            for key in ("treatment", "control", "group_column"):
                if key not in step.parameters:
                    continue
                value = step.parameters[key]
                confirmed = context.confirmed_parameters.get(step.skill_name, {}).get(key)
                if not isinstance(value, str) or (value != confirmed and value not in user_text):
                    raise DeepSeekError("LLM_PLANNER_PARAMETER_ERROR", "Group/column values must come from user text or confirmed context.")
                if key in {"treatment", "control"} and groups and value not in groups:
                    raise DeepSeekError("LLM_PLANNER_PARAMETER_ERROR", "Comparison group is not in the provided group inventory.")
            merged = {**context.confirmed_parameters.get(step.skill_name, {}), **step.parameters}
            if merged.get("treatment") is not None and merged.get("treatment") == merged.get("control"):
                raise DeepSeekError("LLM_PLANNER_PARAMETER_ERROR", "Treatment and control must differ.")
        try:
            workflow = WorkflowPlan(project_id=context.project_id, user_request=user_text, steps=decision.steps)
        except ValidationError:
            raise DeepSeekError("LLM_PLANNER_SCHEMA_ERROR", "Plan failed the existing WorkflowPlan validation.") from None
        check = self.validator.validate(workflow, context)
        if not check.valid:
            raise DeepSeekError("LLM_PLANNER_PLAN_INVALID", "Local dependency validator rejected the proposed plan; it was not repaired or executed.",
                                details={"error_codes": sorted({issue.code for issue in check.errors})})
        return PlannerResponse(decision, workflow, check, response, prompt_hash)
