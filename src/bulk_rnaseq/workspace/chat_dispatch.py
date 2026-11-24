"""UI-independent adapters for existing Workflow and Customization services."""

from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING
from bulk_rnaseq.customization.engine import CustomizationEngine
from bulk_rnaseq.customization.catalog import SKILLS as CUSTOMIZABLE_SKILLS
from bulk_rnaseq.customization.planner import CustomizationPlanner
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workspace.customization_sources import imported_annotations, resolve_customization_source
from bulk_rnaseq.workflow.context import WorkflowContext
from bulk_rnaseq.workflow.executor import WorkflowExecutor
from bulk_rnaseq.workflow.final_response import WorkflowFinalResponseBuilder
from bulk_rnaseq.workflow.history import ProjectHistory
from bulk_rnaseq.workflow.planner import WorkflowPlanner
from bulk_rnaseq.workspace import FileRole, ProjectError
from bulk_rnaseq.workspace.chat_router import ChatRoute
from bulk_rnaseq.workspace.result_manager import ResultManager, SKILL_LABELS
from bulk_rnaseq.workspace.result_resolver import ResultResolver
from bulk_rnaseq.workflow.models import WorkflowPlan, WorkflowStep, ProjectResultBinding
from bulk_rnaseq.workflow.validator import PlanValidator

if TYPE_CHECKING:
    from bulk_rnaseq.workspace.chat_controller import ChatResult


def add_usage(result: "ChatResult", usage: dict[str, Any] | None, count: int = 1) -> None:
    result.request_count += count
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = (usage or {}).get(key)
        if isinstance(value, int):
            result.usage[key] = result.usage.get(key, 0) + value


class ChatDispatch:
    def __init__(self, controller: Any, runner: Any, key: str, notify: Callable, input_snapshot: dict[str, Any]) -> None:
        self.controller, self.runner, self.key, self.notify = controller, runner, key, notify
        self.manager, self.service = controller.manager, controller.service
        self.input_snapshot = input_snapshot

    def check_inputs_unchanged(self) -> None:
        if self.manager.open_project(self.key).metadata.files.model_dump() != self.input_snapshot:
            raise ProjectError("PROJECT_CHANGED", "规划期间项目输入绑定已改变；请检查后提交新请求。")

    def workflow(self, result: "ChatResult", context: Any, groups: list[str]) -> None:
        result.stage = "planning"
        self.notify("planning", None)
        project = self.manager.get_project_context(self.key)
        host = WorkflowContext.from_project_context(project, confirmed_parameters=context.confirmed_parameters,
                                                    history=ProjectHistory(self.manager, self.key))
        planner = WorkflowPlanner(self.runner.client, self.runner.registry)
        shared = self.controller.workflow_service
        planned = (shared.plan(self.key, result.user_message, planner=planner) if shared else
                   planner.plan(result.user_message, context=host, available_groups=groups))
        add_usage(result, planned.response.usage)
        if planned.decision.status != "ready":
            result.status = planned.decision.status
            result.missing = planned.decision.missing_parameters
            result.assistant_message = ("还需要补充：" + ", ".join(result.missing) + "。当前分组：" + ", ".join(groups)
                                        if result.status == "needs_input" else "当前不支持该分析请求。未执行任何步骤。")
            return
        self.execute_plan(result, planned.validation, context)

    def reuse(self, result: "ChatResult", route: ChatRoute, context: Any,
              source_run_id: str | None = None) -> None:
        """Model selects semantics; host selects a verified identity and binds real artifacts."""
        result.stage, result.selected_skill = "source_selection", route.target_skill
        enrichment = route.target_skill in {"rnaseq.go", "rnaseq.kegg"}
        required = ("deg_csv", "filtered_csv") if enrichment else ("deg_csv",)
        resolution = ResultResolver(self.manager, self.key).find_compatible_result(skill_name="rnaseq.deg",
            required_outputs=required, run_id=source_run_id or route.source_run_id,
            treatment=route.treatment, control=route.control, selection=route.source_reference)
        if resolution.status != "resolved":
            result.status, result.error_code = "needs_input", resolution.error_code
            result.source_options = [{"run_id": r.run_id,
                "label": f"{r.comparison} — {r.created_at.isoformat()} — {r.run_id}"} for r in resolution.candidates]
            result.assistant_message = ("找到多个兼容 DEG，请选择来源；不会默认使用最新结果。" if resolution.candidates
                else "当前项目没有可核验的兼容 DEG 来源。请先完成差异分析，或明确比较方向/来源。")
            if resolution.error_code in {"STALE_DEG_RESULT", "DEG_INPUT_VERSION_UNVERIFIED"}:
                result.assistant_message = "已有 DEG 的输入版本不同或无法核验。请先用当前 counts 和 metadata 执行 DEG。"
            return
        source = resolution.result
        result.error_code, result.details, result.missing = None, {}, []
        result.source_run_id = source.run_id
        result.source_label = f"{source.comparison} — {source.run_id}"
        full = ProjectResultBinding(run_id=source.run_id, output="deg_csv")
        bindings = ({"gene_list": ProjectResultBinding(run_id=source.run_id, output="filtered_csv",
                        gene_subset=route.gene_subset), "analysis_genes": full} if enrichment
                    else {"deg_result_file": full})
        plan = WorkflowPlan(project_id=self.key, user_request=result.user_message, steps=[WorkflowStep(
            step_id="reuse", skill_name=route.target_skill, parameters=route.parameters, input_bindings=bindings)])
        project = self.manager.get_project_context(self.key)
        host = WorkflowContext.from_project_context(project, confirmed_parameters=context.confirmed_parameters,
            history=ProjectHistory(self.manager, self.key))
        validation = PlanValidator(self.runner.registry).validate(plan, host)
        if not validation.valid:
            result.status, result.error_code = "needs_input", validation.errors[0].code
            result.assistant_message = "来源已找到，但执行条件未满足：" + ", ".join(e.code for e in validation.errors)
            result.details = {"errors": [e.model_dump() for e in validation.errors]}
            return
        self.execute_plan(result, validation, context, current_deg=resolution.current_input_sha256)
        if not resolution.current_input_sha256:
            result.assistant_message += f" 本次使用指定的历史 DEG {source.run_id}。"
        if len(result.steps) == 1:
            result.run_id = result.steps[0].get("run_id")

    def execute_plan(self, result: "ChatResult", validation: Any, context: Any, *,
                     current_deg: dict[str, str] | None = None) -> None:
        project = self.manager.get_project_context(self.key)
        result.workflow_id = validation.validated_plan.workflow_id
        result.steps = [{"step_id": step.step_id, "skill_name": step.skill_name, "status": "pending",
                         "dependencies": validation.dependencies[step.step_id]} for step in validation.validated_plan.steps]
        self.check_inputs_unchanged()
        # Reuse import provenance checks; the DAG validator only checks readable paths.
        required = {binding.key for bindings in validation.resolved_bindings.values()
                    for binding in bindings.values() if binding.source == "project_input"}
        for role in required:
            self.service._record(self.manager.open_project(self.key), FileRole(role))
        output = project.output_root / "workflows" / result.workflow_id
        def progress(step_id: str, skill: str, status: str) -> None:
            self.notify("step_" + status, f"{step_id} · {SKILL_LABELS.get(skill, skill)}")
        shared = self.controller.workflow_service
        workflow = (shared.run(self.key, validation.validated_plan, current_deg=current_deg) if shared else
            WorkflowExecutor(self.runner.registry, self.manager, self.key,
                rscript_executable=context.rscript_executable, timeout=context.timeout_seconds or 600).execute(validation, progress=progress, current_deg=current_deg))
        result.stage, result.status = "workflow_execution", workflow.status
        result.steps = [{"step_id": step, "skill_name": record["skill_name"], "status": record["status"],
                         "run_id": record.get("run_id"), "dependencies": record.get("depends_on", []),
                         "error_code": record.get("error_code"), "message": record.get("message")}
                        for step, record in workflow.step_records.items()]
        for step, source in workflow.step_results.items():
            result.warnings.extend(source.get("warnings", []))
            self._artifacts(result, source.get("outputs", {}), prefix=step + ".")
        self.notify("summarizing", None)
        final = WorkflowFinalResponseBuilder(self.runner.client).build(workflow)
        add_usage(result, final.usage, final.request_count)
        write_json(output / "final_response.json", final.model_dump(mode="json"))
        result.final_response_status = final.status
        result.assistant_message = final.final_response or "分析记录已保存，但最终总结未通过验证。请查看 Results，不要重复运行。"
        if final.error_code:
            result.error_code = final.error_code
        result.summary = {"completed_steps": workflow.completed_steps, "failed_steps": workflow.failed_steps,
                          "skipped_steps": workflow.skipped_steps}

    def _artifacts(self, result: "ChatResult", outputs: dict[str, str], *, prefix: str = "") -> None:
        browser = ResultManager(self.manager, self.key)
        for label, value in outputs.items():
            path = browser.validate_result_path(value)
            result.artifacts.append({"name": prefix + label, "path": path.relative_to(browser.project_root).as_posix()})

    def annotation_files(self) -> list[Path]:
        return imported_annotations(self.manager, self.key)

    def customization(self, result: "ChatResult", route: ChatRoute, context: Any,
                      source_run_id: str | None = None) -> None:
        result.selected_skill, result.stage = route.target_skill, "source_selection"
        if route.target_skill not in CUSTOMIZABLE_SKILLS:
            result.status = "unsupported"
            result.assistant_message = "这个 Skill 尚未提供安全定制参数；不会动态生成或执行脚本。"
            return
        browser = ResultManager(self.manager, self.key)
        selection = resolve_customization_source(self.manager, self.key, route, source_run_id)
        candidates = selection.candidates
        if selection.status == "resolved":
            source_run_id = selection.result.run_id
        else:
            source_run_id = None
        if source_run_id is None:
            result.status, result.error_code = "needs_input", selection.error_code or "MISSING_UPSTREAM_DEPENDENCY"
            result.source_options = [{"run_id": r.run_id, "label": f"{r.comparison or SKILL_LABELS.get(r.skill_name, r.skill_name)} — {r.run_id}"} for r in candidates]
            result.assistant_message = "请选择要定制的分析结果。" if candidates else "当前项目没有兼容的成功结果，请先完成相应分析。"
            return
        source = browser.get_run(source_run_id)
        result.source_run_id = source_run_id
        result.source_label = f"{source.comparison or SKILL_LABELS.get(source.skill_name, source.skill_name)} — {source_run_id}"
        engine = CustomizationEngine(self.manager, self.key, self.runner.registry,
            rscript_executable=context.rscript_executable, timeout=context.timeout_seconds or 600)
        files = self.annotation_files() if route.target_skill == "rnaseq.deg" else []
        self.notify("planning_customization", route.target_skill)
        planned = CustomizationPlanner(self.runner.client, self.runner.registry).plan(result.user_message,
            target_skill=route.target_skill, available_annotations=engine.available_annotations(files))
        add_usage(result, planned.response.usage)
        self.check_inputs_unchanged()
        self.notify("customizing", route.target_skill)
        # Sorting/direction-only transformations do not need annotation content.
        if planned.decision.mode == "annotation_filter" and not planned.decision.parameters.get("keywords"):
            files = []
        report = engine.execute(planned, source_run_id=source_run_id, annotation_files=files,
                                current_deg=selection.current_input_sha256)
        result.status, result.error_code = report["status"], report.get("error_code")
        result.customization_id = report["customization_id"]
        result.changed_parameters = report["changed_parameters"]
        result.stage = "customization_execution"
        evidence = report.get("skill_result") or {}
        result.run_id = evidence.get("run_id")
        result.summary, result.warnings = evidence.get("summary", {}), evidence.get("warnings", [])
        self._artifacts(result, report["outputs"])
        if report["status"] == "succeeded":
            result.assistant_message = "Customization completed. 基于：" + result.source_label
            if report["mode"] == "annotation_filter":
                result.assistant_message += (f"\nSubset: {planned.decision.parameters.get('gene_subset', 'all')}"
                    + "\nFilter: " + ", ".join(report["annotation_keywords"])
                    + f"\nMatched genes: {result.summary['n_matched_genes']}。这是实际注释文字匹配，不是功能验证。")
        elif report["status"] == "unsupported":
            result.assistant_message = "此定制目前无法安全支持。动态脚本定制需要隔离执行环境。\n" + report.get("reason", "")
        else:
            result.assistant_message = "定制失败，源结果保留。" + report.get("message", "请查看错误码。")
