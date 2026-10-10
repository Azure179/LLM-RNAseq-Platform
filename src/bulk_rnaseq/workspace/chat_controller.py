"""One persisted chat entry for single Skill, Workflow and controlled customization.

No natural-language routing, statistical calculation, shell execution or retries.
"""

from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.execution_context import ExecutionContext
from bulk_rnaseq.llm.single_request import SingleRequestRunner
from bulk_rnaseq.llm.intent_catalog import build_catalog, defaults_for
from bulk_rnaseq.logging_config import run_logger
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import FileImportService, ProjectError, ProjectManager
from bulk_rnaseq.workspace.chat_store import ChatStore, now
from bulk_rnaseq.workspace.readiness import PREFLIGHT_CHECKS
from bulk_rnaseq.workspace.chat_router import ChatRoute, ChatRouter
from bulk_rnaseq.workspace.chat_dispatch import ChatDispatch, add_usage


class ChatResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["succeeded", "partial", "failed", "needs_input", "unsupported"] = "failed"
    request_id: str
    user_message: str = ""
    assistant_message: str = ""
    selected_skill: str | None = None
    run_id: str | None = None
    backend_request_id: str | None = None
    stage: str = "preflight"
    error_code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, str]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    request_count: int = 0
    duration_seconds: float = 0
    final_response_status: str | None = None
    request_type: str = "single_skill"
    workflow_id: str | None = None
    customization_id: str | None = None
    source_run_id: str | None = None
    source_label: str = ""
    source_options: list[dict[str, str]] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    changed_parameters: dict[str, Any] = Field(default_factory=dict)


def default_runner(env_file: Path) -> SingleRequestRunner:
    return SingleRequestRunner(DeepSeekClient(load_deepseek_config(env_file)), create_default_registry())


class ChatController:
    def __init__(self, manager: ProjectManager, env_file: Path, *,
                 runner_factory: Callable[[], SingleRequestRunner] | None = None,
                 enable_routing: bool = True) -> None:
        self.manager = manager
        self.service = FileImportService(manager)
        self.runner_factory = runner_factory or (lambda: default_runner(env_file))
        self.enable_routing = enable_routing

    def history(self, key: str) -> list[dict[str, Any]]:
        return ChatStore(self.manager, key).history()

    def _context(self, key: str, store: ChatStore) -> ExecutionContext:
        context = self.service.build_execution_context(key)
        artifact_file = store.path("chat_upstream.json")
        if not artifact_file.exists():
            return context
        record = store.read(artifact_file)
        current = self.manager.open_project(key).metadata.files.model_dump()
        if record.get("inputs") != {name: current[name] for name in ("count_matrix", "metadata")}:
            return context  # File replacement/removal invalidates implicit upstream binding.
        outputs = record.get("outputs", {})
        for value in outputs.values():
            path = self.manager._safe_path(context.project_root / value["path"]).resolve()
            if not path.is_relative_to(context.output_root) or not path.is_file() or sha256(path) != value["sha256"]:
                return context
        bindings = {name: dict(values) for name, values in context.bindings.items()}
        full, selected = outputs.get("deg_csv"), outputs.get("filtered_csv")
        if full:
            for name in ("rnaseq.volcano", "rnaseq.heatmap"):
                bindings[name]["deg_result_file"] = full["path"]
        if full and selected:
            for name in ("rnaseq.go", "rnaseq.kegg"):
                bindings[name].update(gene_list=selected["path"], analysis_genes=full["path"])
        return context.model_copy(update={"bindings": bindings})

    def submit(self, project_key: str | None, text: str, request_id: str, *,
               progress: Callable[[str, str | None], None] | None = None,
               source_run_id: str | None = None) -> ChatResult:
        result = ChatResult(request_id=request_id)
        if not project_key:
            result.status, result.error_code = "needs_input", "PROJECT_REQUIRED"
            result.assistant_message = "Please create or open a project first."
            return result
        started = perf_counter()
        completed = False
        owned_request = False
        store = ChatStore(self.manager, project_key)
        try:
            with store.lock():
                previous = store.previous(request_id)
                resumed_route = None
                if previous:
                    if previous.get("status") == "completed":
                        return ChatResult.model_validate(previous["result"])
                    if previous.get("status") == "awaiting_source":
                        result = ChatResult.model_validate(previous["result"])
                        if source_run_id is None:
                            return result
                        if text != result.user_message or source_run_id not in {v["run_id"] for v in result.source_options}:
                            raise ProjectError("INVALID_SOURCE_SELECTION", "请选择此请求列出的当前项目来源。")
                        resumed_route = ChatRoute.model_validate(previous["route"])
                        result.source_options = []
                    else:
                        raise ProjectError("REQUEST_ALREADY_SUBMITTED", "这条请求已经提交或曾中断；请先检查 Results，不会自动重跑。")
                elif source_run_id is not None:
                    raise ProjectError("INVALID_SOURCE_SELECTION", "没有等待来源选择的请求。")
                # Configuration is local-only; never retain it in chat/session/UI.
                runner = self.runner_factory()
                if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                    raise ProjectError("INVALID_CHAT_INPUT", "请输入 1–4000 字符的完整分析或定制需求。")
                safe_text = runner.client.sanitize_for_audit(text)
                if safe_text != text:
                    raise ProjectError("INVALID_CHAT_INPUT", "请勿在聊天中输入 API Key。")
                result.user_message = safe_text
                # Refuse corrupt history before creating an analysis side effect.
                store.history()
                context = self._context(project_key, store)
                self.manager.get_project_context(project_key)
                readiness = self.service.get_project_readiness(project_key)
                groups = self.service.get_imported_file_info(project_key, "metadata").get("summary", {}).get("group_names", [])
                inputs_snapshot = self.manager.open_project(project_key).metadata.files.model_dump()
                store.save_request(request_id, {"status": "submitted", "timestamp": now()})
                owned_request = True
                user_row = {"role": "user", "content": safe_text, "timestamp": now(), "skill_name": None,
                            "run_id": None, "status": "submitted", "request_id": request_id}
                store.append([user_row])

                def notify(stage: str, skill: str | None) -> None:
                    if progress:
                        try:
                            progress(stage, skill)
                        except Exception:
                            pass  # UI disconnect cannot change the outcome of an analysis.

                def pre_execute(name: str, validated: dict[str, Any]) -> None:
                    current = self.manager.open_project(project_key).metadata.files.model_dump()
                    if current != inputs_snapshot:
                        raise SkillError("PROJECT_CHANGED", "输入绑定已改变，请检查项目后再提交新的请求。")
                    if readiness[name]["errors"]:
                        error = readiness[name]["errors"][0]
                        raise SkillError(error["error_code"], error["message"], details=error.get("details", {}))
                    check = PREFLIGHT_CHECKS.get(name)
                    if check:
                        defaults = defaults_for(build_catalog(runner.registry)[name]["input_schema"])
                        check({**defaults, **validated})

                route = resumed_route
                from bulk_rnaseq.workspace.result_manager import ResultManager
                has_data = any(context.bindings.values()) or bool(ResultManager(self.manager, project_key).list_runs())
                if self.enable_routing and route is None and has_data:
                    notify("routing", None)
                    route, response = ChatRouter(runner.client, runner.registry).route(safe_text)
                    add_usage(result, response.usage)
                if route:
                    result.request_type = route.request_type
                if route and route.request_type != "single_skill":
                    dispatch = ChatDispatch(self, runner, project_key, notify, inputs_snapshot)
                    if route.request_type == "workflow":
                        dispatch.workflow(result, context, groups)
                    elif route.request_type == "customization":
                        dispatch.customization(result, route, context, source_run_id)
                    else:
                        result.status = route.request_type
                        result.assistant_message = "请明确要做的分析或要修改的结果。" if route.request_type == "needs_input" else "当前不支持该请求，未执行任何分析。"
                elif not any(context.bindings.values()):
                    result.status, result.error_code = "needs_input", "MISSING_PROJECT_INPUTS"
                    result.assistant_message = "请先在 Files 导入 Count Matrix 和 Metadata，或准备分析所需的项目输入。"
                else:
                    outcome = runner.run(safe_text, context, progress=notify, before_execute=pre_execute)
                    execution, source = outcome.execution, outcome.execution.skill_result or {}
                    result.status, result.error_code = outcome.status, outcome.error_code
                    result.stage, result.selected_skill = execution.stage, execution.skill_name
                    result.details = {key: value for key, value in execution.details.items() if key in {
                        "missing_in_metadata", "extra_in_metadata", "missing_in_counts", "available",
                        "requested", "group_column", "sample_id", "row", "field"}}
                    result.backend_request_id, result.run_id = execution.request_id, source.get("run_id")
                    result.missing = execution.missing_parameters
                    add_usage(result, outcome.usage, outcome.request_count)
                    result.final_response_status = outcome.final.status if outcome.final else None
                    result.summary, result.warnings = source.get("summary", {}), source.get("warnings", [])
                    result.assistant_message = outcome.final_response
                    if outcome.status == "needs_input":
                        result.assistant_message = "还需要补充：" + ", ".join(result.missing) + "。请重新完整描述单项分析请求。"
                        if groups:
                            result.assistant_message += " 当前分组：" + ", ".join(groups) + "。"
                    elif outcome.status == "unsupported":
                        result.assistant_message = "当前分析入口未支持该请求，未执行分析。" if self.enable_routing else "当前仅支持已注册的 Bulk RNA-seq 单项分析；不支持单细胞或多步骤请求。请一次描述一项分析。"
                    elif outcome.status == "failed":
                        if source.get("status") == "succeeded":
                            result.assistant_message = "分析文件已生成，但最终总结未成功。请查看 Results，不要直接重复分析。"
                        elif (outcome.error_code or "").startswith("LLM_"):
                            result.assistant_message = "AI service is temporarily unavailable；请查看错误码，未进行自动重试。"
                        else:
                            result.assistant_message = "分析未完成：" + (execution.message or "请查看错误码及本地运行日志。")
                    if source.get("status") == "succeeded":
                        output_records = {}
                        for label, value in source.get("outputs", {}).items():
                            path = self.manager._safe_path(Path(value)).resolve()
                            if not path.is_relative_to(context.output_root) or not path.is_file():
                                raise ProjectError("ARTIFACT_PATH_INVALID", "输出文件不在当前项目 results 内。")
                            relative = path.relative_to(context.project_root).as_posix()
                            result.artifacts.append({"name": label, "path": relative})
                            if label in {"deg_csv", "filtered_csv"}:
                                output_records[label] = {"path": relative, "sha256": sha256(path)}
                        if execution.skill_name == "rnaseq.deg" and output_records:
                            write_json(store.path("chat_upstream.json"), {
                                "inputs": {name: inputs_snapshot[name] for name in ("count_matrix", "metadata")},
                                "run_id": result.run_id, "outputs": output_records})
                result.duration_seconds = round(perf_counter() - started, 3)
                result = ChatResult.model_validate(runner.client.sanitize_for_audit(result.model_dump(mode="json")))
                assistant_row = {"role": "assistant", "content": result.assistant_message, "timestamp": now(),
                                 "skill_name": result.selected_skill, "run_id": result.run_id, "status": result.status,
                                 "request_id": request_id, "workflow_id": result.workflow_id, "customization_id": result.customization_id,
                                 "request_type": result.request_type, "result": result.model_dump(mode="json")}
                # Persist completion BEFORE history append; a storage failure must
                # never turn an already-run request into an automatically retryable one.
                store.save_request(request_id, {"status": "awaiting_source" if result.source_options else "completed",
                    "result": result.model_dump(mode="json"), "route": route.model_dump() if route else None})
                completed = True
                store.save_assistant(assistant_row)
                with run_logger(self.manager._safe_path(context.project_root / "logs/chat.log"), request_id) as logger:
                    logger.info("project=%s request=%s skill=%s run_id=%s status=%s final=%s duration=%s usage=%s requests=%s user=%s",
                                project_key, request_id, result.selected_skill, result.run_id, result.status,
                                result.final_response_status, result.duration_seconds, result.usage, result.request_count, repr(safe_text))
                notify("completed", result.selected_skill)
        except (ProjectError, DeepSeekError, SkillError) as error:
            if completed:
                result.warnings.append({"code": "CHAT_AUDIT_IO_ERROR", "message": "分析状态已保存，但聊天展示或日志写入失败；请查看 Results，不要重跑。", "details": {}})
            else:
                result.status, result.error_code, result.assistant_message = "failed", error.code, str(error)
                if isinstance(error, DeepSeekError) and error.code == "LLM_PLANNER_PLAN_INVALID":
                    codes = error.details.get("error_codes", [])
                    if any(code in {"MISSING_PROJECT_INPUT", "MISSING_UPSTREAM_DEPENDENCY", "MISSING_BUSINESS_PARAMETER"} for code in codes):
                        result.status = "needs_input"
                        result.assistant_message = "计划缺少执行依赖，请先在 Files 补齐输入或明确所需的上游分析：" + ", ".join(codes)
                elif isinstance(error, DeepSeekError) and error.code.startswith("LLM_"):
                    result.assistant_message = "AI service is temporarily unavailable 或响应未通过校验；未自动重试。错误码：" + error.code
        except Exception:
            if completed:
                result.warnings.append({"code": "CHAT_AUDIT_IO_ERROR", "message": "分析状态已保存，但聊天展示或日志写入失败；请查看 Results，不要重跑。", "details": {}})
            else:
                result.status, result.error_code = "failed", "CHAT_OPERATION_FAILED"
                result.assistant_message = "请求未完成；请检查 Results 与项目日志，不会自动重新执行。"
        if owned_request and not completed:
            try:
                store.save_request(request_id, {"status": "completed", "result": result.model_dump(mode="json")})
                store.save_assistant({"role": "assistant", "content": result.assistant_message, "timestamp": now(),
                    "request_id": request_id, "status": result.status, "run_id": result.run_id,
                    "skill_name": result.selected_skill, "result": result.model_dump(mode="json")})
            except (ProjectError, OSError):
                pass  # The submitted marker still prevents a repeat after storage failure.
        return result
