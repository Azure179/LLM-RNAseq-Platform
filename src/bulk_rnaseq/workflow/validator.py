"""Public deterministic validation gate; delegates graph rules to the existing resolver."""

import logging
from typing import Any

from pydantic import ValidationError

from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.workspace.models import ProjectContext
from .context import HistoryLookup, WorkflowContext
from .models import PlanIssue, PlanValidationResult, WorkflowPlan
from .resolver import DependencyResolver

LOGGER = logging.getLogger(__name__)


class PlanValidator:
    """No repair or execution. Successful output is a detached canonical plan.

    Declared inventory supports offline planning. A context made from real
    ProjectContext rechecks files on every validation call. Neither mode promises
    biological correctness, provenance compatibility or runtime readiness.
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self.resolver = DependencyResolver(registry)

    def validate(self, plan: WorkflowPlan | dict[str, Any], context: WorkflowContext) -> PlanValidationResult:
        if not isinstance(context, WorkflowContext):
            return self.resolver.validate(plan, context)
        if context.project is not None and not isinstance(context.project, ProjectContext):
            return PlanValidationResult(valid=False, errors=[PlanIssue(
                code="INVALID_WORKFLOW_CONTEXT", message="Expected a host-owned ProjectContext.")])
        try:
            snapshot = WorkflowPlan.model_validate(plan.model_dump() if isinstance(plan, WorkflowPlan) else plan)
        except ValidationError:
            return self.resolver.validate(plan, context)
        if context.project is not None:
            if context.project.project_key != context.project_id or snapshot.project_id != context.project_id:
                return PlanValidationResult(valid=False, errors=[PlanIssue(
                    code="PROJECT_CONTEXT_MISMATCH", message="Plan, inventory and bound project must have the same identity.")])
            # Replace the old inventory; do not union with stale declared roles.
            context = WorkflowContext.from_project_context(context.project,
                confirmed_parameters=context.confirmed_parameters, history=context.history)
        result = self.resolver.validate(snapshot, context)
        if context.project is not None:
            result.scope = "project_files_and_dependency_contract"
            result.warnings = [*snapshot.warnings,
                "Bound project input files were rechecked for existence, readability and containment. "
                "Data contents, historical provenance and runtime readiness still require execution-time checks."]
        if result.valid:
            # This is deterministic resolution of defaults/bindings already
            # accepted above, never adding a Skill, repairing edges or changing IDs.
            resolved_steps = [step.model_copy(update={
                "parameters": result.resolved_parameters[step.step_id],
                "input_bindings": result.resolved_bindings[step.step_id],
                "depends_on": result.dependencies[step.step_id],
            }) for step in snapshot.steps]
            result.validated_plan = WorkflowPlan.model_validate(
                snapshot.model_copy(update={"steps": resolved_steps}).model_dump())
        LOGGER.info("Plan validation complete: workflow_id=%s valid=%s scope=%s error_codes=%s",
                    snapshot.workflow_id, result.valid, result.scope, [error.code for error in result.errors])
        return result

    def validate_project(self, plan: WorkflowPlan | dict[str, Any], project: ProjectContext, *,
                         confirmed_parameters: dict[str, dict[str, Any]] | None = None,
                         history: HistoryLookup | None = None) -> PlanValidationResult:
        """Host convenience entry point; use a freshly obtained ProjectContext."""
        context = WorkflowContext(project.project_key, frozenset(),
                                  {} if confirmed_parameters is None else confirmed_parameters, history, project)
        return self.validate(plan, context)
