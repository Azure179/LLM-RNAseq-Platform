"""Read-only, project-scoped discovery of existing SkillResult documents."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import mimetypes
import os
from pathlib import Path, PureWindowsPath
from typing import Any

from bulk_rnaseq.llm.json_utils import unique_object, reject_constant
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.workspace import ProjectError, ProjectManager
from bulk_rnaseq.workspace.chat_store import ChatStore


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
SOURCES = {"summary.json", "skill_result.json", "failure.json", "tool_execution.json"}
COLLECTION_SOURCES = {"workflow_result.json", "customization.json"}
SKILL_LABELS = {"rnaseq.deg": "Differential Expression", "rnaseq.pca": "PCA",
                "rnaseq.volcano": "Volcano Plot", "rnaseq.heatmap": "Heatmap",
                "rnaseq.go": "GO Enrichment", "rnaseq.kegg": "KEGG Enrichment",
                "rnaseq.filter_by_annotation": "Annotation Filter", "rnaseq.enrichment_plot": "Enrichment Replot"}


@dataclass(frozen=True)
class ArtifactInfo:
    artifact_id: str
    filename: str
    kind: str
    relative_path: str | None
    size_bytes: int | None
    error_code: str | None = None


@dataclass
class RunInfo:
    run_id: str
    skill_name: str
    status: str
    created_at: str
    time_source: str
    source: str
    directory: str
    parameters: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[ArtifactInfo] = field(default_factory=list)
    backend_request_id: str | None = None
    comparison: str = ""
    stage: str | None = None
    error_code: str | None = None
    message: str | None = None
    triggered_by: str | None = None
    final_response_status: str | None = None
    browser_warnings: list[str] = field(default_factory=list)
    result_type: str = "Analysis"
    workflow_id: str | None = None
    customization_id: str | None = None
    source_run_id: str | None = None
    step_id: str | None = None
    dependencies: list[str] = field(default_factory=list)

    @property
    def has_warning(self) -> bool:
        return bool(self.warnings or self.browser_warnings or any(a.error_code for a in self.artifacts))


def kind_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return {".png": "IMAGE", ".jpg": "IMAGE", ".jpeg": "IMAGE", ".csv": "TABLE",
            ".tsv": "TABLE", ".json": "JSON", ".txt": "TEXT", ".log": "TEXT"}.get(suffix, "UNKNOWN")


def display_value(value: Any, depth: int = 0) -> Any:
    """Bound JSON display and hide local absolute paths / runtime diagnostics."""
    if depth > 6:
        return "[nested content omitted]"
    if isinstance(value, dict):
        excluded = {"stdout", "stderr", "process", "processes", "llm_response", "reasoning_content", "api_key"}
        result = {str(k): display_value(v, depth + 1) for k, v in list(value.items())[:80] if k.lower() not in excluded}
        if len(value) > 80:
            result["…"] = f"{len(value)} keys; compact preview"
        return result
    if isinstance(value, list):
        result = [display_value(v, depth + 1) for v in value[:30]]
        return result + ([f"… {len(value)} items total"] if len(value) > 30 else [])
    if isinstance(value, str):
        if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
            return "[local path hidden]"
        return value[:2000] + ("…" if len(value) > 2000 else "")
    return value


class ResultManager:
    def __init__(self, manager: ProjectManager, project_key: str, *, max_entries: int = 10000) -> None:
        self.manager, self.project_key = manager, project_key
        self.project_root = manager.open_project(project_key).root
        self.root = self.project_root / "results"
        if type(max_entries) is not int or max_entries < 1:
            raise ProjectError("INVALID_PARAMETER", "max_entries 必须为正整数。")
        self.max_entries = max_entries
        self.runs: list[RunInfo] = []
        self.diagnostics: list[str] = []
        self._loaded = False
        self.collections: list[dict[str, Any]] = []

    def validate_result_path(self, value: str | Path, *, must_exist: bool = True) -> Path:
        raw = Path(value)
        if ".." in raw.parts or (not raw.is_absolute() and PureWindowsPath(str(value)).drive):
            raise ProjectError("RESULT_PATH_NOT_ALLOWED", "结果路径必须位于当前项目 results 内。")
        path = self.manager._safe_path(self.root / raw).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ProjectError("RESULT_PATH_NOT_ALLOWED", "结果路径必须位于当前项目 results 内。")
        if must_exist and not path.is_file():
            raise ProjectError("ARTIFACT_MISSING", "Artifact file is missing.")
        return path

    def _json(self, path: Path, limit: int = MAX_JSON_BYTES) -> dict[str, Any]:
        path = self.validate_result_path(path)
        try:
            with path.open("rb") as handle:
                data = handle.read(limit + 1)
            if len(data) > limit:
                raise ProjectError("RESULT_JSON_TOO_LARGE", "JSON 超过预览上限；可单独下载原文件。")
            value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_object, parse_constant=reject_constant)
            if not isinstance(value, dict):
                raise ValueError("Expected object")
            return value
        except (ValueError, TypeError, RecursionError, OSError):
            raise ProjectError("RESULT_JSON_INVALID", "结果 JSON 无法读取；原文件未修改。") from None

    def _scan(self) -> list[Path]:
        sources: list[Path] = []
        pending, scanned = [self.validate_result_path(self.root, must_exist=False)], 0
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > self.max_entries:
                            self.diagnostics.append("结果目录达到扫描上限，当前列表可能不完整。")
                            return sources
                        path = Path(entry.path)
                        try:
                            self.validate_result_path(path, must_exist=False)
                        except ProjectError:
                            self.diagnostics.append("跳过链接或越界路径。")
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(path)
                        elif entry.is_file(follow_symlinks=False) and entry.name in SOURCES | COLLECTION_SOURCES:
                            sources.append(path)
            except OSError:
                self.diagnostics.append("部分结果目录无法读取。")
        return sources

    def _artifact(self, label: str, value: str | Path) -> ArtifactInfo:
        filename = Path(str(value)).name
        try:
            path = self.validate_result_path(value, must_exist=False)
            relative = path.relative_to(self.root).as_posix()
            if not path.is_file():
                return ArtifactInfo(label, filename, kind_for(filename), relative, None, "ARTIFACT_MISSING")
            return ArtifactInfo(label, filename, kind_for(filename), relative, path.stat().st_size)
        except (ProjectError, OSError):
            return ArtifactInfo(label, filename, kind_for(filename), None, None, "RESULT_PATH_NOT_ALLOWED")

    def _from_source(self, path: Path, payload: dict[str, Any], *, backend_id: str | None = None) -> RunInfo:
        source = SkillResult.from_dict(payload) if payload.get("schema_version") == "2.0" else SkillResult.from_legacy_summary(payload)
        params = source.inputs.get("parameters", source.metadata.get("parameters", {}))
        stamp = source.metadata.get("started_at") or payload.get("started_at")
        time_source = "recorded"
        try:
            stamp = datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError):
            stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            time_source = "file modification time (analysis time unavailable)"
        run = RunInfo(run_id=source.run_id or backend_id or "record:" + path.relative_to(self.root).as_posix(),
                      skill_name=source.skill_name, status=source.status, created_at=stamp, time_source=time_source,
                      source=path.relative_to(self.root).as_posix(), directory=path.parent.relative_to(self.root).as_posix(),
                      parameters=params if isinstance(params, dict) else {}, summary=source.summary, warnings=source.warnings,
                      backend_request_id=backend_id, error_code=source.error_code, message=source.message)
        if run.parameters.get("treatment") and run.parameters.get("control"):
            run.comparison = f"{run.parameters['treatment']} vs {run.parameters['control']}"
        # Phase 4 summaries declare portable relative artifacts. Use these only
        # for legacy summaries; canonical v2 outputs remain authoritative.
        outputs = source.outputs
        if payload.get("schema_version") != "2.0":
            outputs = {key: str(path.parent / value) for key, value in payload.get("artifacts", {}).items()}
        run.artifacts = [self._artifact(key, value) for key, value in outputs.items()]
        if not any(a.relative_path == run.source for a in run.artifacts):
            run.artifacts.append(self._artifact("result_record", path))
        return run

    def refresh(self) -> list[RunInfo]:
        self.runs, self.diagnostics, self.collections = [], [], []
        files = self._scan()
        documents: dict[Path, RunInfo] = {}
        # Summary documents are authoritative; embedded tool results are fallback.
        order = {"summary.json": 0, "skill_result.json": 1, "failure.json": 2}
        for path in sorted((p for p in files if p.name in order), key=lambda p: (order[p.name], str(p))):
            if path.parent in documents:
                continue
            try:
                run = self._from_source(path, self._json(path))
                documents[path.parent] = run
                self.runs.append(run)
            except (ProjectError, KeyError, ValueError, TypeError, OSError):
                self.diagnostics.append(f"无法识别结果记录：{path.relative_to(self.root).as_posix()}")
        for path in sorted(p for p in files if p.name == "tool_execution.json"):
            try:
                payload = self._json(path)
                backend_id = str(payload.get("request_id") or path.parent.name)
                run = documents.get(path.parent / "skill") or documents.get(path.parent)
                if run is None and isinstance(payload.get("skill_result"), dict):
                    run = self._from_source(path, payload["skill_result"], backend_id=backend_id)
                    self.runs.append(run)
                if run is None:
                    status = payload.get("status", "unknown")
                    if status not in {"failed", "needs_input", "unsupported", "validated"}:
                        status = "unknown"  # A success flag without a SkillResult proves no analysis.
                    run = RunInfo(backend_id, payload.get("skill_name") or "unselected", status,
                                  datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "file modification time",
                                  path.relative_to(self.root).as_posix(), path.parent.relative_to(self.root).as_posix(),
                                  parameters=payload.get("arguments", {}))
                    self.runs.append(run)
                run.backend_request_id = backend_id
                run.stage = payload.get("stage")
                if run.status != "succeeded":
                    run.error_code = payload.get("error_code") or run.error_code
                    run.message = payload.get("message") or run.message
            except (ProjectError, KeyError, ValueError, TypeError, OSError):
                self.diagnostics.append(f"无法读取执行报告：{path.relative_to(self.root).as_posix()}")
        self._load_collections(files)
        try:
            history = ChatStore(self.manager, self.project_key).history()
            for row in history:
                if row["role"] != "assistant":
                    continue
                result = row.get("result", {})
                for run in self.runs:
                    if (row.get("run_id") and row["run_id"] == run.run_id) or (
                        run.backend_request_id and result.get("backend_request_id") == run.backend_request_id
                    ):
                        run.triggered_by = result.get("user_message")
                        run.final_response_status = result.get("final_response_status")
        except ProjectError:
            self.diagnostics.append("聊天记录无法读取；仍可查看分析结果。")
        for run in self.runs:
            if sum(other.run_id == run.run_id for other in self.runs) > 1:
                run.browser_warnings.append("发现重复 run_id，需检查重复的结果目录。")
        self._loaded = True
        return self.list_runs()

    def _load_collections(self, files: list[Path]) -> None:
        from bulk_rnaseq.workflow.models import WorkflowResult
        for path in sorted(p for p in files if p.name in COLLECTION_SOURCES):
            try:
                payload = self._json(path)
                if path.name == "workflow_result.json":
                    workflow = WorkflowResult.model_validate(payload)
                    members = []
                    for index, step_id in enumerate(workflow.execution_order, 1):
                        record = workflow.step_records[step_id]
                        run_id = record.get("run_id")
                        matches = [r for r in self.runs if r.run_id == run_id and
                                   (self.root / r.source).is_relative_to(path.parent)] if run_id else []
                        if not matches and step_id in workflow.step_results:
                            transport = self.validate_result_path(path.parent / "steps" / f"{index:03d}" / "execution_result.json")
                            if self._json(transport) != workflow.step_results[step_id]:
                                raise ValueError("Step transport disagrees with WorkflowResult")
                            run = self._from_source(transport, workflow.step_results[step_id])
                            run.run_id = run_id or f"{workflow.workflow_id}:{step_id}"
                            self.runs.append(run)
                            matches = [run]
                        for run in matches:
                            run.result_type, run.workflow_id, run.step_id = "Workflow", workflow.workflow_id, step_id
                            run.dependencies = record.get("depends_on", [])
                            parents = {source.get("run_id") for source in record.get("upstream_sources", {}).values()
                                       if source.get("source") in {"workflow_step", "project_result"} and source.get("run_id")}
                            if len(parents) == 1:
                                run.source_run_id = next(iter(parents))
                        members.append({"step_id": step_id, "skill_name": record["skill_name"], "run_id": run_id,
                                        "status": record["status"], "dependencies": record.get("depends_on", []),
                                        "error_code": record.get("error_code"), "message": record.get("message")})
                    self.collections.append({"id": workflow.workflow_id, "type": "Workflow", "status": workflow.status,
                        "members": members, "source": path.relative_to(self.root).as_posix()})
                else:
                    identity = payload["customization_id"]
                    if identity != path.parent.name or not isinstance(payload.get("changed_parameters"), dict):
                        raise ValueError("Invalid customization identity")
                    source = payload.get("skill_result") or {}
                    run_id = source.get("run_id")
                    for run in self.runs:
                        if run.run_id == run_id and (self.root / run.source).is_relative_to(path.parent):
                            run.result_type, run.customization_id = "Customization", identity
                            run.source_run_id = payload.get("source_run_id")
                    self.collections.append({"id": identity, "type": "Customization", "status": payload["status"],
                        "run_id": run_id, "source_run_id": payload.get("source_run_id"), "mode": payload["mode"],
                        "changed_parameters": payload["changed_parameters"], "keywords": payload.get("annotation_keywords", []),
                        "source_skill": payload.get("source_skill"), "error_code": payload.get("error_code"),
                        "reason": payload.get("reason", payload.get("message", "")),
                        "source": path.relative_to(self.root).as_posix()})
            except (ProjectError, ValueError, KeyError, TypeError, AttributeError, OSError):
                self.diagnostics.append("无法识别 Workflow/Customization 记录：" + path.relative_to(self.root).as_posix())
        # Inherit a comparison only from the exact declared source within this project.
        for _ in range(len(self.runs)):
            changed = False
            for run in self.runs:
                if not run.comparison and run.source_run_id:
                    parents = [p for p in self.runs if p.run_id == run.source_run_id and p.comparison]
                    if len(parents) == 1:
                        run.comparison, changed = parents[0].comparison, True
            if not changed:
                break

    def list_collections(self, kind: str | None = None) -> list[dict[str, Any]]:
        if not self._loaded:
            self.refresh()
        return [item for item in self.collections if kind is None or item["type"] == kind]

    def list_runs(self, *, skill_name: str | None = None, status: str | None = None, newest_first: bool = True) -> list[RunInfo]:
        if not self._loaded:
            self.refresh()
        runs = [r for r in self.runs if (skill_name is None or r.skill_name == skill_name)
                and (status is None or (r.has_warning if status == "warning" else r.status == status))]
        return sorted(runs, key=lambda r: (r.created_at, r.run_id), reverse=newest_first)

    def list_skill_runs(self, skill_name: str) -> list[RunInfo]:
        return self.list_runs(skill_name=skill_name)

    def get_run(self, run_id: str) -> RunInfo:
        matches = [r for r in self.list_runs() if r.run_id == run_id or r.backend_request_id == run_id]
        if len(matches) != 1:
            raise ProjectError("RUN_NOT_FOUND" if not matches else "DUPLICATE_RUN_ID", "找不到唯一的当前项目分析记录，请刷新 Results。")
        return matches[0]

    def get_run_summary(self, run_id: str) -> dict[str, Any]:
        return self.get_run(run_id).summary

    def list_artifacts(self, run_id: str) -> list[ArtifactInfo]:
        return self.get_run(run_id).artifacts

    def get_artifact(self, run_id: str, artifact_id: str) -> ArtifactInfo:
        artifacts = [a for a in self.list_artifacts(run_id) if a.artifact_id == artifact_id]
        if len(artifacts) != 1:
            raise ProjectError("ARTIFACT_NOT_FOUND", "找不到当前 run 的结果文件。")
        artifact = artifacts[0]
        if artifact.relative_path is None:
            raise ProjectError("RESULT_PATH_NOT_ALLOWED", "结果文件路径不允许访问。")
        # Recheck at every preview/download; discovery is not authorization to read.
        path = self.validate_result_path(artifact.relative_path)
        current = self._artifact(artifact.artifact_id, path)
        if current.error_code:
            raise ProjectError(current.error_code, "结果文件已变化，请刷新后再试。")
        return current

    def read_artifact(self, run_id: str, artifact_id: str, *, limit: int = MAX_DOWNLOAD_BYTES) -> tuple[ArtifactInfo, bytes]:
        artifact = self.get_artifact(run_id, artifact_id)
        if artifact.size_bytes > limit:
            raise ProjectError("ARTIFACT_TOO_LARGE", f"该操作最多读取 {limit // (1024 * 1024)} MiB。")
        try:
            with self.validate_result_path(artifact.relative_path).open("rb") as handle:
                data = handle.read(limit + 1)
            if len(data) > limit:
                raise ProjectError("ARTIFACT_TOO_LARGE", "文件已超过读取上限。")
            return artifact, data
        except OSError:
            raise ProjectError("ARTIFACT_READ_FAILED", "文件暂时无法读取，请刷新。") from None

    def preview_json(self, run_id: str, artifact_id: str) -> dict[str, Any]:
        artifact = self.get_artifact(run_id, artifact_id)
        return display_value(self._json(self.validate_result_path(artifact.relative_path)))

    def download(self, run_id: str, artifact_id: str) -> tuple[str, bytes, str]:
        artifact, data = self.read_artifact(run_id, artifact_id)
        return artifact.filename, data, mimetypes.guess_type(artifact.filename)[0] or "application/octet-stream"

    def preview_table(self, run_id: str, artifact_id: str, *, limit: int = 100) -> Any:
        from bulk_rnaseq.workspace.result_preview import preview_table
        artifact = self.get_artifact(run_id, artifact_id)
        return preview_table(self.validate_result_path(artifact.relative_path), limit=limit)

    def preview_image(self, run_id: str, artifact_id: str) -> bytes:
        from bulk_rnaseq.workspace.result_preview import validate_image
        _, data = self.read_artifact(run_id, artifact_id, limit=20 * 1024 * 1024)
        validate_image(data)
        return data
