"""Host-owned single-Skill execution; reuse existing contracts, bindings and persistence."""

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.tool_adapter import ToolAdapter
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.workflow.contracts import DependencyContracts
from bulk_rnaseq.workflow.history import ProjectHistory
from bulk_rnaseq.workflow.models import GeneSubset, ProjectResultBinding, WorkflowPlan, WorkflowStep
from bulk_rnaseq.workflow.executor import WorkflowExecutor
from bulk_rnaseq.workflow.validator import PlanValidator
from bulk_rnaseq.workspace import FileImportService, FileRole, ProjectManager
from bulk_rnaseq.workspace.result_resolver import ResultResolver


@dataclass
class SkillExecution:
    result: SkillResult
    record: dict[str, Any]
    workflow_id: str
    project_id: str


class SingleSkillService:
    def __init__(self, registry: SkillRegistry, manager: ProjectManager, *,
                 rscript_executable: str = "Rscript", timeout: float = 600) -> None:
        self.registry, self.manager = registry, manager
        self.rscript_executable, self.timeout = rscript_executable, timeout
        self.adapter = ToolAdapter(registry)
        self.contracts = DependencyContracts(registry)

    def upstream_edges(self, name: str) -> list[tuple[str, str, str, str]]:
        # Current downstream mode reuses DEG only; other upstream capabilities
        # (e.g. optional normalized Heatmap output) are not silently selected.
        return sorted(edge for edge in self.contracts.edges if edge[0] == "rnaseq.deg" and edge[2] == name)

    def parameter_schema(self, name: str) -> dict[str, Any]:
        self.registry.get(name)
        if not self.registry.get(name).available:
            raise SkillError("SKILL_UNAVAILABLE", "Skill is not implemented.")
        schema = self.adapter.build_tool_definition(name)["function"]["parameters"]
        if self.upstream_edges(name):
            deg = self.adapter.build_tool_definition("rnaseq.deg")["function"]["parameters"]
            schema["properties"].update({key: deg["properties"][key] for key in ("treatment", "control")})
        if self.contracts.metadata[name].subset_target_inputs:
            from pydantic import TypeAdapter
            schema["properties"]["gene_subset"] = {**TypeAdapter(GeneSubset).json_schema(), "default": "all"}
        return schema

    def execute(self, name: str, project_id: str, parameters: dict[str, Any], *,
                source_run_id: str | None = None) -> SkillExecution:
        schema = self.parameter_schema(name)
        problems = list(Draft202012Validator(schema).iter_errors(parameters))
        if problems:
            raise SkillError("INVALID_PARAMETER", "Parameters do not match the public Skill schema.",
                details={"rules": sorted({str(p.validator) for p in problems})})
        project = self.manager.get_project_context(project_id)
        snapshot = self.manager.open_project(project_id).metadata.files.model_dump()
        service = FileImportService(self.manager)
        context = service.build_execution_context(project_id, registry=self.registry)
        business = dict(parameters)
        bindings = {}
        current_deg = None
        edges = self.upstream_edges(name)
        if edges:
            treatment, control = business.pop("treatment", None), business.pop("control", None)
            if treatment is not None and treatment == control:
                raise SkillError("INVALID_COMPARISON", "Treatment and control must differ.")
            subset = business.pop("gene_subset", "all")
            resolution = ResultResolver(self.manager, project_id).find_compatible_result(
                skill_name="rnaseq.deg", required_outputs=tuple({edge[1] for edge in edges}),
                run_id=source_run_id, treatment=treatment, control=control)
            if resolution.status == "ambiguous":
                raise SkillError("AMBIGUOUS_UPSTREAM_RESULT", "Choose an explicit source_run_id.",
                    details={"candidates": [{"run_id": ref.run_id, "treatment": ref.treatment,
                        "control": ref.control, "created_at": ref.created_at.isoformat()} for ref in resolution.candidates]})
            if resolution.status != "resolved":
                if resolution.error_code not in {None, "MISSING_UPSTREAM_DEPENDENCY"}:
                    raise SkillError(resolution.error_code, "No DEG verified for the current inputs. Run a new DEG or explicitly select a historical run.")
                raise SkillError("RESULT_NOT_FOUND" if source_run_id else "MISSING_UPSTREAM_DEPENDENCY",
                                 "No verified compatible DEG exists in this project.")
            current_deg = resolution.current_input_sha256
            for _, output, _, parameter in edges:
                bindings[parameter] = ProjectResultBinding(run_id=resolution.result.run_id, output=output,
                    gene_subset=subset if parameter in self.contracts.metadata[name].subset_target_inputs else None)
        elif source_run_id is not None:
            raise SkillError("INVALID_PARAMETER", "This Skill does not accept a historical source.")
        plan = WorkflowPlan(project_id=project_id, user_request="Single Skill API request", steps=[WorkflowStep(
            step_id="skill", skill_name=name, parameters=business, input_bindings=bindings)])
        checked = PlanValidator(self.registry).validate_project(plan, project,
            confirmed_parameters=context.confirmed_parameters, history=ProjectHistory(self.manager, project_id))
        if not checked.valid:
            issue = checked.errors[0]
            raise SkillError(issue.code, issue.message, details=issue.details)
        # Exactly the same import-hash gate used by ChatDispatch. Do not accept
        # a readable file whose project import record has become invalid.
        for binding in checked.resolved_bindings["skill"].values():
            if binding.source == "project_input":
                service._record(self.manager.open_project(project_id), FileRole(binding.key))
        if snapshot != self.manager.open_project(project_id).metadata.files.model_dump():
            raise SkillError("PROJECT_INPUT_CHANGED", "Project inputs changed before execution.")
        workflow = WorkflowExecutor(self.registry, self.manager, project_id,
            rscript_executable=self.rscript_executable, timeout=self.timeout).execute(checked, current_deg=current_deg)
        return SkillExecution(SkillResult.from_dict(workflow.step_results["skill"]),
                              workflow.step_records["skill"], workflow.workflow_id, project_id)
