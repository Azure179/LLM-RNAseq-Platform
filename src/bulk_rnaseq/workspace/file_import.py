"""Validated immutable file versions plus atomic bindings; no UI or analysis execution."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.json_utils import reject_constant, unique_object
from bulk_rnaseq.logging_config import run_logger
from bulk_rnaseq.result_schema import json_copy
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.validation import require_input_file
from bulk_rnaseq.workspace.errors import ProjectError
from bulk_rnaseq.workspace.file_models import FileRole, ImportResult
from bulk_rnaseq.workspace.import_validation import (
    check_record_shape, detect_file_role, enrich_file_info, inspect_file, prepare_table, validate_file, warning,
)
from bulk_rnaseq.workspace.manager import ProjectManager
from bulk_rnaseq.workspace.models import ProjectContext, ProjectSnapshot, validate_name, validate_relative_input


LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bulk_rnaseq.llm.execution_context import ExecutionContext


def canonical_role(value: FileRole | str) -> FileRole:
    try:
        return FileRole(value)
    except (ValueError, TypeError):
        raise ProjectError("INVALID_FILE_ROLE", "Select count_matrix, metadata, go_mapping or kegg_mapping.") from None


def failure(action: str, role: FileRole | str, error: Exception, filename: str | None = None) -> ImportResult:
    try:
        canonical = FileRole(role)
    except (ValueError, TypeError):
        canonical = None
    if isinstance(error, (SkillError, ProjectError)):
        code, message = error.code, str(error)
        details = {key: value for key, value in error.details.items() if key != "path"}
    else:
        code, message, details = "FILE_IMPORT_ERROR", "Unable to read, validate or save the selected file.", {}
    warnings = [warning("POSSIBLE_NORMALIZED_MATRIX", "Non-integer expression cannot be used as raw counts; no rounding was performed.")] if code == "NON_INTEGER_COUNT" else []
    return ImportResult(status="failed", action=action, role=canonical, original_filename=filename,
                        error_code=code, message=message, details=details, warnings=warnings)


class FileImportService:
    inspect_file = staticmethod(inspect_file)
    detect_file_role = staticmethod(detect_file_role)

    def __init__(self, manager: ProjectManager) -> None:
        self.manager = manager

    def import_eggnog(self, project_key: str, source_path: Path, *, replace: bool = False,
                      mapping_sheet: str | None = None) -> ImportResult:
        """Generate and atomically bind both mappings; do not execute enrichment."""
        from bulk_rnaseq.workspace.eggnog_import import import_eggnog
        return import_eggnog(self, project_key, source_path, replace=replace, mapping_sheet=mapping_sheet)

    def get_sample_groups(self, project_key: str) -> dict[str, Any]:
        from bulk_rnaseq.workspace.sample_groups import get_sample_groups
        return get_sample_groups(self, project_key)

    def save_sample_groups(self, project_key: str, groups: dict[str, str], *,
                           expected_updated_at: str, replace: bool = False) -> ImportResult:
        from bulk_rnaseq.workspace.sample_groups import save_sample_groups
        return save_sample_groups(self, project_key, groups, expected_updated_at=expected_updated_at, replace=replace)

    def build_execution_context(self, project_key: str, **options: Any) -> "ExecutionContext":
        from bulk_rnaseq.workspace.readiness import build_execution_context
        return build_execution_context(self, project_key, **options)

    def get_project_readiness(self, project_key: str, **options: Any) -> dict[str, Any]:
        from bulk_rnaseq.workspace.readiness import get_project_readiness
        return get_project_readiness(self, project_key, **options)

    @staticmethod
    def _context(project: ProjectSnapshot) -> dict[str, Any]:
        """Refresh from the committed snapshot, without a post-commit IO failure."""
        root = project.root
        paths = {role.value: root / value if (value := getattr(project.metadata.files, role.value)) else None for role in FileRole}
        return json_copy(asdict(ProjectContext(project.key, project.metadata.project_name, root, root / "input",
                                              root / "results", root / "logs", root / "reports", root / "runtime", **paths)))

    def _audit(self, project_key: str, result: ImportResult) -> None:
        try:
            project = self.manager.open_project(project_key)
            with run_logger(self.manager._safe_path(project.root / "logs/import.log"), project_key) as logger:
                logger.info("project=%s role=%s action=%s source_filename=%s stored_path=%s validation_status=%s error_code=%s",
                            project_key, result.role, result.action, json.dumps(result.original_filename),
                            result.stored_path, result.status, result.error_code)
        except (ProjectError, OSError):
            LOGGER.warning("Import audit unavailable; status=%s code=%s", result.status, result.error_code)

    def _record(self, project: ProjectSnapshot, role: FileRole) -> dict[str, Any] | None:
        relative = getattr(project.metadata.files, role.value)
        if relative is None:
            return None
        path = self.manager._safe_path(project.root / relative)
        record_path = self.manager._safe_path(path.parent.parent / "import.json")
        try:
            with record_path.open("rb") as handle:
                content = handle.read(16 * 1024 * 1024 + 1)
            if len(content) > 16 * 1024 * 1024:
                raise ValueError("Oversize import record")
            record = json.loads(content.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
            if record["role"] != role.value or record["stored_path"] != relative:
                raise ValueError("Record/binding mismatch")
            check_record_shape(record, role)
            original = self.manager._safe_path(project.root / validate_relative_input(record["original_path"]))
            if not original.is_relative_to(path.parent.parent):
                raise ValueError("Original must belong to this file version")
            stored_hash = sha256(path)
            original_hash = stored_hash if original == path else sha256(original)
            if stored_hash != record["stored_sha256"] or original_hash != record["source_sha256"]:
                raise ProjectError("IMPORTED_FILE_CHANGED", "Imported file content changed. Validate and replace it explicitly.", details={"role": role.value})
            return record
        except (OSError, ValueError, TypeError, KeyError):
            raise ProjectError("IMPORT_RECORD_INVALID", "Imported file or its validation record is missing/invalid.", details={"role": role.value}) from None

    def _related_records(self, project: ProjectSnapshot, role: FileRole) -> dict[FileRole, dict[str, Any]]:
        # A broken KEGG file must not block an unrelated count/metadata import.
        related = {FileRole.COUNT_MATRIX, FileRole.METADATA} if role in {FileRole.COUNT_MATRIX, FileRole.METADATA} else {FileRole.COUNT_MATRIX, role}
        return {key: record for key in related if key != role and (record := self._record(project, key)) is not None}

    def import_file(self, project_key: str, source_path: Path, role: FileRole | str, *, replace: bool = False,
                    sample_column: str | None = None, group_column: str | None = None,
                    mapping_sheet: str | None = None, expected_updated_at: str | None = None) -> ImportResult:
        """Copy, validate snapshot, compare peers, then publish a new immutable binding."""
        filename = None
        action = "replace" if replace else "import"
        try:
            role = canonical_role(role)
            if type(replace) is not bool:
                raise ProjectError("INVALID_IMPORT_PARAMETER", "replace must be a boolean.")
            if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in (sample_column, group_column, mapping_sheet)):
                raise ProjectError("INVALID_IMPORT_PARAMETER", "Selected column/sheet names must be non-empty strings.")
            if role != FileRole.METADATA and (sample_column is not None or group_column is not None):
                raise ProjectError("INVALID_IMPORT_PARAMETER", "Column selection applies to metadata only.")
            source = Path(source_path)
            try:
                filename = validate_name(source.name)
            except ValueError:
                raise ProjectError("UNSAFE_FILENAME", "The source filename is not safe for project storage.") from None
            allowed = {".csv", ".tsv", ".txt", ".xlsx", ".annotations"}
            if role in {FileRole.COUNT_MATRIX, FileRole.METADATA}:
                allowed.add(".xls")  # Text matrices exported with an Excel-looking suffix.
            if source.suffix.lower() not in allowed:
                raise ProjectError("IMPORT_FORMAT_UNSUPPORTED", "Only supported count/metadata tables and annotation files can be imported; no FASTQ or executable files.")
            require_input_file(source)
            if source.stat().st_size == 0:
                raise ProjectError("IMPORT_EMPTY_FILE", "The selected file is empty.")
            project = self.manager.open_project(project_key)
            if expected_updated_at is not None and project.metadata.updated_at != expected_updated_at:
                raise ProjectError("PROJECT_CHANGED", "项目已改变，请刷新样本列表后重新填写分组。")
            old_path = getattr(project.metadata.files, role.value)
            if old_path is not None and not replace:
                raise ProjectError("FILE_ROLE_ALREADY_BOUND", "This role is already bound. Explicitly choose replace=True.")
            peers = self._related_records(project, role)
            runtime = self.manager._safe_path(project.root / "runtime")
            with TemporaryDirectory(prefix="import-", dir=runtime) as temporary:
                stage = Path(temporary)
                (stage / "original").mkdir()
                original = stage / "original" / filename
                shutil.copyfile(source, original)
                source_hash = sha256(original)
                if sha256(source) != source_hash:
                    raise ProjectError("IMPORT_SOURCE_CHANGED", "Source changed during copying; retry with a stable file.")
                adaptations = []
                selected = original
                if role in {FileRole.COUNT_MATRIX, FileRole.METADATA}:
                    if mapping_sheet is not None:
                        raise ProjectError("INVALID_IMPORT_PARAMETER", "mapping_sheet only applies to annotation XLSX.")
                    (stage / "analysis").mkdir()
                    selected = stage / "analysis" / (role.value + ".csv")
                    adaptations = prepare_table(original, role, selected, sample_column=sample_column, group_column=group_column)
                record = validate_file(selected, role, mapping_sheet=mapping_sheet)
                record["warnings"] = adaptations + record["warnings"]
                info = enrich_file_info(role, record, {**peers, role: record})
                # Both import orders run the same sample-set comparison above.
                base = self.manager._safe_path(project.root / "input" / role.value)
                base.mkdir(exist_ok=True)
                destination = self.manager._safe_path(base / str(uuid4()))
                stored = destination / selected.relative_to(stage)
                relative = stored.relative_to(project.root).as_posix()
                record.update(role=role.value, stored_path=relative, original_filename=filename,
                              original_path=(destination / original.relative_to(stage)).relative_to(project.root).as_posix(),
                              source_sha256=source_hash, stored_sha256=sha256(selected),
                              imported_at=datetime.now(timezone.utc).isoformat(), mapping_sheet=mapping_sheet)
                write_json(stage / "import.json", record)
                stage.rename(destination)
                # On CAS/write failure the new version is unbound and retained; the
                # old binding/file remain intact. No destructive rollback is needed.
                updated = self.manager.commit_file_binding(project_key, role, relative, expected_updated_at=project.metadata.updated_at)
            if old_path:
                info["warnings"].append(warning("PREVIOUS_VERSION_RETAINED", "Old project file version is retained for recovery/history.", previous_path=old_path))
            result = ImportResult(status="succeeded", action=action, role=role, original_filename=filename,
                                  stored_path=relative, **info, context=self._context(updated))
        except (SkillError, ProjectError, OSError, ValueError, TypeError, KeyError) as error:
            result = failure(action, role, error, filename)
        self._audit(project_key, result)
        return result

    def replace_file(self, project_key: str, source_path: Path, role: FileRole | str, **options: Any) -> ImportResult:
        return self.import_file(project_key, source_path, role, replace=True, **options)

    def remove_file(self, project_key: str, role: FileRole | str) -> ImportResult:
        try:
            role = canonical_role(role)
            project = self.manager.open_project(project_key)
            old = getattr(project.metadata.files, role.value)
            if old is None:
                raise ProjectError("FILE_ROLE_NOT_BOUND", "This file role is not bound.")
            updated = self.manager.commit_file_binding(project_key, role, None, expected_updated_at=project.metadata.updated_at)
            result = ImportResult(status="succeeded", action="remove", role=role,
                                  warnings=[warning("PREVIOUS_VERSION_RETAINED", "Binding removed; the project file version remains available.", previous_path=old)],
                                  context=self._context(updated))
        except (SkillError, ProjectError, OSError, ValueError, TypeError) as error:
            result = failure("remove", role, error)
        self._audit(project_key, result)
        return result

    def get_imported_file_info(self, project_key: str, role: FileRole | str) -> dict[str, Any]:
        try:
            role = canonical_role(role)
            project = self.manager.open_project(project_key)
            record = self._record(project, role)
            if record is None:
                return {"status": "missing", "role": role.value, "summary": {}, "warnings": []}
            info = enrich_file_info(role, record, {**self._related_records(project, role), role: record})
            return {"status": "warning" if info["warnings"] else "valid", "role": role.value,
                    "original_filename": record["original_filename"], "stored_path": record["stored_path"],
                    "source_kind": record.get("source_kind", "direct_upload"), **info}
        except (SkillError, ProjectError, OSError, ValueError, TypeError, KeyError) as error:
            failed = failure("inspect", role, error).model_dump(mode="json")
            return {**failed, "status": "invalid"}

    def validate_imported_file(self, project_key: str, role: FileRole | str) -> ImportResult:
        """Full revalidation (no rebind); cached summaries alone are not validation."""
        try:
            role = canonical_role(role)
            project = self.manager.open_project(project_key)
            records = self._related_records(project, role)
            record = self._record(project, role)
            if record is None:
                raise ProjectError("FILE_ROLE_NOT_BOUND", "This file role is not bound.")
            fresh = validate_file(project.root / record["stored_path"], role, mapping_sheet=record["mapping_sheet"])
            info = enrich_file_info(role, fresh, {**records, role: fresh})
            result = ImportResult(status="succeeded", action="validate", role=role, stored_path=record["stored_path"], **info)
        except (SkillError, ProjectError, OSError, ValueError, TypeError, KeyError) as error:
            result = failure("validate", role, error)
        self._audit(project_key, result)
        return result
