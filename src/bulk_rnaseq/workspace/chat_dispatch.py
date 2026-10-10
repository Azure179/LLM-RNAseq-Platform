"""UI-independent adapters for existing Workflow and Customization services."""

from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING
from bulk_rnaseq.customization.engine import CustomizationEngine
from bulk_rnaseq.customization.catalog import SKILLS as CUSTOMIZABLE_SKILLS
from bulk_rnaseq.customization.planner import CustomizationPlanner
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.run_io import write_json, sha256
from bulk_rnaseq.workflow.context import WorkflowContext
from bulk_rnaseq.workflow.executor import WorkflowExecutor
from bulk_rnaseq.workflow.final_response import WorkflowFinalResponseBuilder
from bulk_rnaseq.workflow.history import ProjectHistory
from bulk_rnaseq.workflow.planner import WorkflowPlanner
from bulk_rnaseq.workspace import FileRole, ProjectError
from bulk_rnaseq.workspace.chat_router import ChatRoute
from bulk_rnaseq.workspace.result_manager import ResultManager, SKILL_LABELS

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
        planned = WorkflowPlanner(self.runner.client, self.runner.registry).plan(result.user_message, context=host, available_groups=groups)
        add_usage(result, planned.response.usage)
        if planned.decision.status != "ready":
            result.status = planned.decision.status
            result.missing = planned.decision.missing_parameters
            result.assistant_message = ("还需要补充：" + ", ".join(result.missing) + "。当前分组：" + ", ".join(groups)
                                        if result.status == "needs_input" else "当前不支持该分析请求。未执行任何步骤。")
            return
        result.workflow_id = planned.plan.workflow_id
        result.steps = [{"step_id": step.step_id, "skill_name": step.skill_name, "status": "pending",
                         "dependencies": planned.validation.dependencies[step.step_id]} for step in planned.plan.steps]
        self.check_inputs_unchanged()
        # Reuse import provenance checks; the DAG validator only checks readable paths.
        required = {binding.key for bindings in planned.validation.resolved_bindings.values()
                    for binding in bindings.values() if binding.source == "project_input"}
        for role in required:
            self.service._record(self.manager.open_project(self.key), FileRole(role))
        output = project.output_root / "workflows" / result.workflow_id
        def progress(step_id: str, skill: str, status: str) -> None:
            self.notify("step_" + status, f"{step_id} · {SKILL_LABELS.get(skill, skill)}")
        workflow = WorkflowExecutor(self.runner.registry, self.manager, self.key,
            rscript_executable=context.rscript_executable, timeout=context.timeout_seconds or 600).execute(planned.validation, progress=progress)
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
        if not final.final_response:
            result.error_code = final.error_code
        result.summary = {"completed_steps": workflow.completed_steps, "failed_steps": workflow.failed_steps,
                          "skipped_steps": workflow.skipped_steps}

    def _artifacts(self, result: "ChatResult", outputs: dict[str, str], *, prefix: str = "") -> None:
        browser = ResultManager(self.manager, self.key)
        for label, value in outputs.items():
            path = browser.validate_result_path(value)
            result.artifacts.append({"name": prefix + label, "path": path.relative_to(browser.project_root).as_posix()})

    def annotation_files(self) -> list[Path]:
        project = self.manager.open_project(self.key)
        paths: list[Path] = []
        seen_hashes: set[str] = set()
        for role in (FileRole.GO_MAPPING, FileRole.KEGG_MAPPING):
            record = self.service._record(project, role)  # Existing binding/hash validation, not an untrusted path.
            if record:
                candidate = self.manager._safe_path(project.root / record["original_path"])
                checksum = sha256(candidate)
                if checksum not in seen_hashes:
                    paths.append(candidate)
                    seen_hashes.add(checksum)  # eggNOG import retains one original copy per mapping role.
        return paths

    def customization(self, result: "ChatResult", route: ChatRoute, context: Any,
                      source_run_id: str | None = None) -> None:
        result.selected_skill, result.stage = route.target_skill, "source_selection"
        if route.target_skill not in CUSTOMIZABLE_SKILLS:
            result.status = "unsupported"
            result.assistant_message = "这个 Skill 尚未提供安全定制参数；不会动态生成或执行脚本。"
            return
        browser = ResultManager(self.manager, self.key)
        history = ProjectHistory(self.manager, self.key)
        source_skill = "rnaseq.filter_by_annotation" if route.source_kind == "filtered_deg" else route.target_skill
        candidates = [r for r in browser.list_runs(skill_name=source_skill, status="succeeded")
                      if history.lookup(self.key, r.run_id) is not None]
        if not candidates and route.target_skill == "rnaseq.volcano":
            candidates = browser.list_runs(skill_name="rnaseq.deg", status="succeeded")
        by_id = {r.run_id: r for r in candidates}
        if source_run_id is not None and source_run_id not in by_id:
            raise ProjectError("CUSTOMIZATION_SOURCE_UNAVAILABLE", "所选来源已不可用或不属于当前项目/结果类型。")
        if source_run_id is None and route.source_reference == "recent":
            for row in reversed(self.controller.history(self.key)):
                if row["role"] != "assistant":
                    continue
                previous = row.get("result", {})
                referenced = {previous.get("run_id"), *[s.get("run_id") for s in previous.get("steps", [])]}
                compatible = referenced & set(by_id)
                if compatible:
                    if len(compatible) == 1:
                        source_run_id = next(iter(compatible))
                    break  # Multiple compatible steps in one response are ambiguous.
        if source_run_id is None and len(candidates) == 1:
            source_run_id = candidates[0].run_id
        if source_run_id is None:
            result.status, result.error_code = "needs_input", "SOURCE_SELECTION_REQUIRED" if candidates else "MISSING_UPSTREAM_DEPENDENCY"
            result.source_options = [{"run_id": r.run_id, "label": f"{r.comparison or SKILL_LABELS.get(r.skill_name, r.skill_name)} — {r.run_id}"} for r in candidates]
            result.assistant_message = "请选择要定制的分析结果。" if candidates else "当前项目没有兼容的成功结果，请先完成相应分析。"
            return
        source = by_id[source_run_id]
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
        report = engine.execute(planned, source_run_id=source_run_id, annotation_files=files)
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
