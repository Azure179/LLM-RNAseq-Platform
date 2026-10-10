"""Deterministic contract validation only. No path injection, subprocess or execution."""

import heapq
import logging
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.intent_catalog import defaults_for, is_path
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.result_schema import json_copy
from bulk_rnaseq.validation import validate_parameters
from bulk_rnaseq.workspace.models import validate_name
from bulk_rnaseq.workspace.errors import ProjectError
from .context import WorkflowContext
from .contracts import DependencyContracts
from .models import (InputBinding, PlanIssue, PlanValidationResult, ProjectInputBinding,
                     ProjectResultBinding, StepOutputBinding, WorkflowPlan)

LOGGER = logging.getLogger(__name__)


def topological_order(dependencies: dict[str, set[str]]) -> list[str] | None:
    """Kahn's algorithm, lexical tie-break: input list order cannot alter independent steps."""
    remaining = {key: len(value) for key, value in dependencies.items()}
    children: dict[str, set[str]] = {key: set() for key in dependencies}
    for child, parents in dependencies.items():
        for parent in parents:
            children[parent].add(child)
    ready = [key for key, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        parent = heapq.heappop(ready)
        ordered.append(parent)
        for child in sorted(children[parent]):
            remaining[child] -= 1
            if remaining[child] == 0:
                heapq.heappush(ready, child)
    return ordered if len(ordered) == len(dependencies) else None


class DependencyResolver:
    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry
        self.contracts = DependencyContracts(registry)

    def validate(self, plan: WorkflowPlan | dict[str, Any], context: WorkflowContext) -> PlanValidationResult:
        """Return safe structured errors, never an executable request or fake SkillResult."""
        try:
            validate_name(context.project_id)
            confirmed = json_copy(context.confirmed_parameters)
            roles = {role for metadata in self.contracts.metadata.values() for role in metadata.project_inputs.values()}
            if (not isinstance(context.available_inputs, (set, frozenset)) or not context.available_inputs <= roles
                    or not isinstance(confirmed, dict)
                    or any(name not in self.contracts.schemas or not isinstance(values, dict)
                           for name, values in confirmed.items())):
                raise ValueError("Invalid host inventory or confirmed parameters")
        except (AttributeError, TypeError, ValueError, OverflowError):
            return PlanValidationResult(valid=False, errors=[PlanIssue(
                code="INVALID_WORKFLOW_CONTEXT", message="Expected a valid host-owned project inventory and finite parameter objects.")])
        try:
            # Revalidate even model instances, as nested containers remain mutable.
            plan = WorkflowPlan.model_validate(plan.model_dump() if isinstance(plan, WorkflowPlan) else plan)
        except ValidationError as error:
            return PlanValidationResult(valid=False, errors=[PlanIssue(
                code="INVALID_WORKFLOW_PLAN", message="Plan does not match WorkflowPlan schema.",
                details={"fields": [".".join(map(str, item["loc"])) for item in error.errors()]})])
        errors: list[PlanIssue] = []

        def issue(code: str, message: str, step_id: str | None = None, **details: Any) -> None:
            errors.append(PlanIssue(code=code, message=message, step_id=step_id, details=details))

        if plan.project_id != context.project_id:
            issue("PROJECT_CONTEXT_MISMATCH", "Plan and host context belong to different projects.")
        steps = {step.step_id: step for step in plan.steps}
        dependencies: dict[str, set[str]] = {step_id: set() for step_id in steps}
        data_parents: dict[str, set[str]] = {step_id: set() for step_id in steps}
        resolved: dict[str, dict[str, InputBinding]] = {}
        parameters: dict[str, dict[str, Any]] = {}
        for step in plan.steps:
            for parent in step.depends_on:
                if parent not in steps:
                    issue("UNKNOWN_WORKFLOW_STEP", "Dependency step does not exist.", step.step_id, dependency=parent)
                else:
                    dependencies[step.step_id].add(parent)
            for binding in step.input_bindings.values():
                if isinstance(binding, StepOutputBinding):
                    if binding.step_id not in steps:
                        issue("UNKNOWN_WORKFLOW_STEP", "Input source step does not exist.", step.step_id,
                              dependency=binding.step_id)
                    else:
                        dependencies[step.step_id].add(binding.step_id)
                        data_parents[step.step_id].add(binding.step_id)
            try:
                entry = self.registry.get(step.skill_name)
                if not entry.available:
                    issue("SKILL_UNAVAILABLE", "Skill is not implemented.", step.step_id)
                elif step.skill_name not in self.contracts.schemas:
                    issue("INVALID_WORKFLOW_CONTRACT", "Registry changed after resolver initialization; rebuild the resolver.", step.step_id)
            except SkillError as error:
                issue(error.code, str(error), step.step_id)

        order = topological_order(dependencies)
        if order is None:
            issue("WORKFLOW_CYCLE_DETECTED", "Workflow contains a cyclic dependency.")
        for step in plan.steps:
            if set(step.depends_on) - data_parents[step.step_id]:
                issue("INVALID_EXECUTION_DEPENDENCY", "Execution dependencies must be supported by data bindings.", step.step_id)
            if step.skill_name not in self.contracts.schemas:
                continue
            schema = self.contracts.schemas[step.skill_name]
            meta = self.contracts.metadata[step.skill_name]
            props = schema["properties"]
            scalar_props = {key: value for key, value in props.items() if not is_path(value)}
            supplied = {**confirmed.get(step.skill_name, {}), **step.parameters}
            scalar_schema = {"type": "object", "properties": scalar_props, "additionalProperties": False,
                             "required": [key for key in schema["required"] if key in scalar_props]}
            merged = {key: value for key, value in defaults_for(schema).items() if key in scalar_props}
            merged.update(supplied)
            problems = list(Draft202012Validator(scalar_schema).iter_errors(merged))
            # JSON Schema treats 2.0 as an integer, while existing Python Skills
            # require an actual int for top_n/top_n_labels. Reject, do not coerce.
            integer_errors = [key for key, value in merged.items() if key in scalar_props
                              and scalar_props[key].get("type") == "integer" and type(value) is not int]
            if problems or integer_errors:
                issue("INVALID_WORKFLOW_PARAMETERS", "Business parameters do not match the existing Skill schema; paths/host settings are forbidden.",
                      step.step_id, rules=sorted({str(problem.validator) for problem in problems}),
                      missing=[key for key in scalar_schema["required"] if merged.get(key) is None],
                      integer_fields=integer_errors)
            else:
                parameters[step.step_id] = merged
                if step.skill_name == "rnaseq.deg":
                    try:
                        # Existing pure parameter validation, not run_deg or R.
                        # The positive sentinel timeout is not an execution setting.
                        validate_parameters(merged["treatment"], merged["control"],
                                            merged["alpha"], merged["lfc_threshold"], 1)
                    except SkillError as error:
                        issue(error.code, str(error), step.step_id)
            bindings = dict(step.input_bindings)
            for key in bindings.keys() - meta.project_inputs.keys():
                issue("INVALID_INPUT_BINDING", "Binding target is not a data parameter.", step.step_id, parameter=key)
            # Required root data can be bound from host inventory; never invent an upstream step.
            for key in schema["required"]:
                if key in meta.project_inputs and key not in bindings:
                    role = meta.project_inputs[key]
                    if role in context.available_inputs:
                        bindings[key] = ProjectInputBinding(key=role)
                    else:
                        code = ("MISSING_UPSTREAM_DEPENDENCY" if self.contracts.has_upstream(step.skill_name, key)
                                else "MISSING_PROJECT_INPUT")
                        issue(code, "Required data is unavailable; provide an explicit binding.", step.step_id,
                              missing=[role], parameter=key)
            # Existing Heatmap/ORA oneOf chooses exactly one data input, not a new rule copy.
            alternatives = [branch["required"][0] for branch in schema.get("oneOf", [])]
            if alternatives:
                selected = [key for key in alternatives if key in bindings]
                if not selected:
                    candidates = [key for key in alternatives if meta.project_inputs[key] in context.available_inputs]
                    if len(candidates) == 1:
                        key = candidates[0]
                        bindings[key] = ProjectInputBinding(key=meta.project_inputs[key])
                    else:
                        issue("AMBIGUOUS_INPUT_BINDING" if candidates else "MISSING_UPSTREAM_DEPENDENCY",
                              "Choose exactly one declared expression/background source.", step.step_id, missing=alternatives)
                elif len(selected) != 1:
                    issue("CONFLICTING_INPUT_BINDINGS", "Exactly one alternative data input must be bound.", step.step_id,
                          parameters=selected)
            for key, binding in bindings.items():
                if key not in meta.project_inputs:
                    continue
                if isinstance(binding, ProjectInputBinding):
                    if binding.key != meta.project_inputs[key]:
                        issue("INCOMPATIBLE_INPUT_BINDING", "Project input role does not match this parameter.", step.step_id, parameter=key)
                    elif binding.key not in context.available_inputs:
                        issue("MISSING_PROJECT_INPUT", "Bound project input is unavailable.", step.step_id, missing=[binding.key])
                    continue
                source_skill: str | None = None
                if isinstance(binding, StepOutputBinding) and binding.step_id in steps:
                    source_skill = steps[binding.step_id].skill_name
                elif isinstance(binding, ProjectResultBinding):
                    historical = None
                    if context.history is not None and plan.project_id == context.project_id:
                        try:
                            historical = context.history.lookup(context.project_id, binding.run_id)
                        except (SkillError, ProjectError, OSError, ValueError):
                            issue("HISTORY_LOOKUP_FAILED", "Cannot verify the requested historical run.", step.step_id)
                    if (historical is None or historical.project_id != context.project_id
                            or historical.run_id != binding.run_id or historical.status != "succeeded"):
                        issue("MISSING_UPSTREAM_DEPENDENCY", "A successful exact run from this project is required.", step.step_id)
                    elif binding.output not in historical.outputs:
                        issue("INVALID_OUTPUT_BINDING", "Historical run does not contain the requested output.", step.step_id)
                    else:
                        source_skill = historical.skill_name
                if source_skill is not None:
                    if binding.output not in self.contracts.outputs.get(source_skill, set()):
                        issue("INVALID_OUTPUT_BINDING", "Output is not declared by the source Skill.", step.step_id, output=binding.output)
                    elif (source_skill, binding.output, step.skill_name, key) not in self.contracts.edges:
                        issue("INCOMPATIBLE_OUTPUT_BINDING", "Manifest does not declare this output as a compatible data source.", step.step_id, parameter=key)
                    elif binding.gene_subset is not None and (
                        key not in meta.subset_target_inputs
                        or binding.output not in self.contracts.metadata[source_skill].subset_source_outputs
                    ):
                        issue("INVALID_GENE_SUBSET_BINDING", "Gene subset selection requires declared significant DEG source and query target.", step.step_id)
            resolved[step.step_id] = bindings
        LOGGER.info("Workflow dependency validation: workflow_id=%s valid=%s steps=%d error_codes=%s",
                    plan.workflow_id, not errors, len(steps), [error.code for error in errors])
        return PlanValidationResult(valid=not errors, execution_order=order if not errors else [],
                                    dependencies={key: sorted(value) for key, value in dependencies.items()},
                                    resolved_bindings=resolved, resolved_parameters=parameters, errors=errors,
                                    warnings=[*plan.warnings, "Contract validation only; data contents, history provenance and runtime readiness require execution-time checks."])
