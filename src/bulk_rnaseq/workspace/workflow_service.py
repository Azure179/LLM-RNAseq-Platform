"""Host-owned Workflow entry points. Planning and execution are explicitly separate."""

from pathlib import Path

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.workflow.context import WorkflowContext
from bulk_rnaseq.workflow.executor import WorkflowExecutor
from bulk_rnaseq.workflow.history import ProjectHistory
from bulk_rnaseq.workflow.models import PlanValidationResult, WorkflowPlan, WorkflowResult
from bulk_rnaseq.workflow.planner import PlannerResponse, WorkflowPlanner
from bulk_rnaseq.workflow.validator import PlanValidator
from bulk_rnaseq.workspace import FileImportService, FileRole, ProjectManager
from bulk_rnaseq.workspace.result_resolver import ResultResolver


class WorkflowService:
    def __init__(self, registry: SkillRegistry, manager: ProjectManager, *,
                 rscript_executable: str = "Rscript", timeout: float = 600,
                 env_file: Path | None = None) -> None:
        self.registry, self.manager = registry, manager
        self.rscript_executable, self.timeout, self.env_file = rscript_executable, timeout, env_file
        self.validator = PlanValidator(registry)

    def create_planner(self) -> WorkflowPlanner:
        # Lazy: metadata/run endpoints do not read credentials or require an API key.
        return WorkflowPlanner(DeepSeekClient(load_deepseek_config(self.env_file)), self.registry)

    def context(self, project_id: str) -> WorkflowContext:
        project = self.manager.get_project_context(project_id)
        imported = FileImportService(self.manager).build_execution_context(project_id, registry=self.registry)
        return WorkflowContext.from_project_context(project, confirmed_parameters=imported.confirmed_parameters,
            history=ProjectHistory(self.manager, project_id))

    def _imports(self, project_id: str, roles: set[str]) -> None:
        service = FileImportService(self.manager)
        project = self.manager.open_project(project_id)
        for role in roles:
            if service._record(project, FileRole(role)) is None:
                raise SkillError("MISSING_PROJECT_INPUT", "Required imported input is unavailable.", details={"missing": [role]})

    @staticmethod
    def _require_valid(checked: PlanValidationResult) -> None:
        if not checked.valid:
            # Preserve all Phase 7 issues, not just an HTTP-only replacement error.
            first = checked.errors[0]
            raise SkillError(first.code, "Workflow validation failed.",
                details={"errors": [issue.model_dump(mode="json") for issue in checked.errors]})

    def _validate(self, plan: WorkflowPlan, context: WorkflowContext) -> PlanValidationResult:
        checked = self.validator.validate(plan, context)
        self._require_valid(checked)
        roles = {binding.key for values in checked.resolved_bindings.values() for binding in values.values()
                 if binding.source == "project_input"}
        self._imports(context.project_id, roles)
        # Exact, project-local historical binding; never guess a latest source.
        resolver = ResultResolver(self.manager, context.project_id)
        for values in checked.resolved_bindings.values():
            for binding in values.values():
                if binding.source != "project_result":
                    continue
                historical = context.history.lookup(context.project_id, binding.run_id)
                if historical is None:
                    raise SkillError("MISSING_UPSTREAM_DEPENDENCY", "Historical source is unavailable.")
                resolution = resolver.find_compatible_result(skill_name=historical.skill_name,
                    run_id=binding.run_id, required_outputs=(binding.output,))
                if resolution.status != "resolved":
                    raise SkillError("AMBIGUOUS_UPSTREAM_RESULT" if resolution.status == "ambiguous"
                        else "MISSING_UPSTREAM_DEPENDENCY", "Historical source is not uniquely verifiable.")
        return checked

    def plan(self, project_id: str, user_request: str, *, planner: WorkflowPlanner | None = None) -> PlannerResponse:
        context = self.context(project_id)
        snapshot = self.manager.open_project(project_id).metadata.files.model_dump()
        self._imports(project_id, set(context.available_inputs))
        groups = FileImportService(self.manager).get_imported_file_info(project_id, "metadata").get("summary", {}).get("group_names", [])
        planned = (planner or self.create_planner()).plan(user_request, context=context, available_groups=groups)
        if snapshot != self.manager.open_project(project_id).metadata.files.model_dump():
            raise SkillError("PROJECT_INPUT_CHANGED", "Project bindings changed during planning.")
        if planned.plan is not None:
            from dataclasses import replace
            checked = self._validate(planned.plan, self.context(project_id))
            planned = replace(planned, plan=checked.validated_plan, validation=checked)
        return planned

    def run(self, project_id: str, plan: WorkflowPlan, *, current_deg: dict[str, str] | None = None) -> WorkflowResult:
        context = self.context(project_id)
        snapshot = self.manager.open_project(project_id).metadata.files.model_dump()
        checked = self._validate(plan, context)
        if snapshot != self.manager.open_project(project_id).metadata.files.model_dump():
            raise SkillError("PROJECT_INPUT_CHANGED", "Project bindings changed before execution.")
        return WorkflowExecutor(self.registry, self.manager, project_id,
            rscript_executable=self.rscript_executable, timeout=self.timeout).execute(checked, current_deg=current_deg)
