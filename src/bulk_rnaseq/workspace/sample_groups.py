"""User-confirmed sample grouping, converted through the normal metadata importer."""

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TYPE_CHECKING

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.validation import MISSING, scan_metadata
from bulk_rnaseq.workspace.errors import ProjectError
from bulk_rnaseq.workspace.file_models import FileRole, ImportResult

if TYPE_CHECKING:
    from bulk_rnaseq.workspace.file_import import FileImportService


def get_sample_groups(service: "FileImportService", project_key: str) -> dict[str, Any]:
    """Read verified count sample names and previously saved, explicit groups."""
    project = service.manager.open_project(project_key)
    counts = service._record(project, FileRole.COUNT_MATRIX)
    if counts is None:
        raise ProjectError("COUNT_MATRIX_REQUIRED", "请先导入 Count Matrix，系统才能列出样本供你填写分组。")
    saved: dict[str, str] = {}
    warning = None
    try:
        metadata = service._record(project, FileRole.METADATA)
        if metadata:
            path = service.manager._safe_path(project.root / metadata["stored_path"])
            saved = scan_metadata(path, metadata["summary"]["group_column"])
    except (ProjectError, SkillError):
        warning = "原 Metadata 无法验证，未加载旧分组；可重新填写并确认替换。"
    return {"samples": [{"sample_id": sample, "group": saved.get(sample, "")}
                        for sample in counts["summary"]["sample_names"]],
            "expected_updated_at": project.metadata.updated_at,
            "metadata_bound": project.metadata.files.metadata is not None,
            "warning": warning}


def save_sample_groups(service: "FileImportService", project_key: str, groups: dict[str, str], *,
                       expected_updated_at: str, replace: bool = False) -> ImportResult:
    from bulk_rnaseq.workspace.file_import import failure

    try:
        project = service.manager.open_project(project_key)
        if not isinstance(expected_updated_at, str) or project.metadata.updated_at != expected_updated_at:
            raise ProjectError("PROJECT_CHANGED", "项目已改变，请刷新样本列表后重新填写分组。")
        counts = service._record(project, FileRole.COUNT_MATRIX)
        if counts is None:
            raise ProjectError("COUNT_MATRIX_REQUIRED", "请先导入 Count Matrix。")
        samples = counts["summary"]["sample_names"]
        if not isinstance(groups, dict) or set(groups) != set(samples):
            raise ProjectError("SAMPLE_MISMATCH", "分组必须完整对应当前 count matrix 的全部样本；不能添加或漏掉样本。")
        for sample in samples:
            value = groups[sample]
            if not isinstance(value, str) or value.strip().upper() in MISSING:
                raise ProjectError("MISSING_GROUP", "请为每个样本填写分组，不能留空或使用 NA 等缺失值。", details={"sample_id": sample})
            if value != value.strip() or any(char in value for char in "\r\n\t"):
                raise ProjectError("INVALID_GROUP", "组名不能包含首尾空格、换行或 Tab。", details={"sample_id": sample})
        runtime = service.manager._safe_path(project.root / "runtime")
        with TemporaryDirectory(prefix="sample-groups-", dir=runtime) as temporary:
            source = Path(temporary) / "user_sample_groups.csv"
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["sample_id", "condition"])
                writer.writerows((sample, groups[sample]) for sample in samples)
            # Reuses normal validation, immutable original/analysis versions, audit,
            # replacement confirmation and the final compare-and-swap commit.
            result = service.import_file(project_key, source, FileRole.METADATA, replace=replace,
                                         expected_updated_at=expected_updated_at)
        if result.status == "succeeded":
            result.message = "分组已保存，Metadata 已自动生成并绑定；每组样本数见摘要。"
        return result
    except (ProjectError, SkillError, OSError, ValueError, TypeError) as error:
        result = failure("save_sample_groups", FileRole.METADATA, error, "user_sample_groups.csv")
        service._audit(project_key, result)
        return result
