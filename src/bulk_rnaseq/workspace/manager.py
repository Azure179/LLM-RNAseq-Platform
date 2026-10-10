"""Filesystem project lifecycle; no file import, analysis, API calls or UI state."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import logging
from pathlib import Path
from typing import Any, Callable, ParamSpec, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from bulk_rnaseq.llm.json_utils import reject_constant, unique_object
from bulk_rnaseq.logging_config import run_logger
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workspace.errors import ProjectError
from bulk_rnaseq.workspace.file_models import FileRole
from bulk_rnaseq.workspace.models import (
    FILE_ROLES, PROJECT_DIRS, ProjectContext, ProjectMetadata, ProjectSnapshot, validate_name,
)


LOGGER = logging.getLogger(__name__)
P = ParamSpec("P")
T = TypeVar("T")
MAX_METADATA_BYTES = 64 * 1024


def _safe_io(function: Callable[P, T]) -> Callable[P, T]:
    """Keep expected filesystem failures structured; never expose OS exception text."""
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except OSError:
            LOGGER.error("Project operation failed: operation=%s code=PROJECT_IO_ERROR", function.__name__)
            raise ProjectError("PROJECT_IO_ERROR", "Unable to access project storage. Check permissions and available disk space.",
                               details={"operation": function.__name__}) from None
    return wrapped


class ProjectManager:
    @_safe_io
    def __init__(self, workspace_root: Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, path: Path) -> Path:
        """Reject traversal, symlinks and Windows junctions, including internal links."""
        if self.root.resolve() != self.root:
            raise ProjectError("PROJECT_PATH_NOT_ALLOWED", "Workspace root has changed; reopen the workspace.")
        try:
            parts = path.relative_to(self.root).parts
        except ValueError:
            raise ProjectError("PROJECT_PATH_NOT_ALLOWED", "Project path must stay inside the workspace.") from None
        cursor = self.root
        for part in parts:
            cursor /= part
            if cursor.is_symlink() or cursor.is_junction():
                raise ProjectError("PROJECT_PATH_NOT_ALLOWED", "Linked project paths are not supported.")
        if not path.resolve().is_relative_to(self.root):
            raise ProjectError("PROJECT_PATH_NOT_ALLOWED", "Project path must stay inside the workspace.")
        return path

    def _project_path(self, key: str) -> Path:
        try:
            validate_name(key)
        except ValueError:
            raise ProjectError("INVALID_PROJECT_NAME", "Use a portable project name without path separators or reserved characters.") from None
        return self._safe_path(self.root / key)

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        """One cooperating writer per workspace; stale locks are never auto-deleted."""
        lock = self._safe_path(self.root / ".workspace.lock")
        try:
            handle = lock.open("x", encoding="utf-8")
        except FileExistsError:
            raise ProjectError("WORKSPACE_BUSY", "Another project update is active, or a stale workspace lock requires inspection.") from None
        try:
            with handle:
                handle.write(str(uuid4()))
            yield
        finally:
            lock.unlink(missing_ok=True)

    def _read(self, key: str) -> ProjectSnapshot:
        root = self._project_path(key)
        if not root.is_dir():
            raise ProjectError("PROJECT_NOT_FOUND", "The requested project does not exist.", details={"project_key": key})
        path = self._safe_path(root / "project.json")
        try:
            with path.open("rb") as handle:
                content = handle.read(MAX_METADATA_BYTES + 1)
            if len(content) > MAX_METADATA_BYTES:
                raise ValueError("Oversize project metadata")
            data = json.loads(content.decode("utf-8-sig"), object_pairs_hook=unique_object, parse_constant=reject_constant)
            if not isinstance(data, dict) or set(data) != set(ProjectMetadata.model_fields):
                raise ValueError("Incomplete metadata")
            if type(data["schema_version"]) is not int:
                raise ValueError("Invalid schema version")
            metadata = ProjectMetadata.model_validate(data)
        except FileNotFoundError:
            raise ProjectError("PROJECT_METADATA_MISSING", "Project metadata is missing; existing files were not modified.", details={"project_key": key}) from None
        except (ValueError, TypeError, UnicodeError, ValidationError):
            raise ProjectError("PROJECT_METADATA_INVALID", "Project metadata is invalid or unsupported; existing files were not modified.", details={"project_key": key}) from None
        for directory in PROJECT_DIRS:
            if not self._safe_path(root / directory).is_dir():
                raise ProjectError("PROJECT_LAYOUT_INVALID", "A required project directory is missing.", details={"directory": directory, "project_key": key})
        for relative in metadata.files.model_dump().values():
            if relative is not None:
                self._safe_path(root / relative)
        return ProjectSnapshot(key, root, metadata)

    def _name_available(self, name: str, *, excluding: str | None = None) -> None:
        self._project_path(name)  # Validate display names with the same portable policy.
        for entry in self.root.iterdir():
            if entry.name.startswith(".") or entry.name == excluding:
                continue
            if entry.name.casefold() == name.casefold():
                raise ProjectError("PROJECT_EXISTS", "A project or reserved directory with this name already exists.")
            if entry.is_dir():
                try:
                    project = self._read(entry.name)
                except ProjectError:
                    continue  # Corrupt/unmanaged directories remain reserved by folder name.
                if project.metadata.project_name.casefold() == name.casefold():
                    raise ProjectError("PROJECT_EXISTS", "A project with this display name already exists.")

    def _event(self, project: ProjectSnapshot, action: str) -> None:
        # Failure of supplementary logging cannot undo an already committed update.
        try:
            with run_logger(self._safe_path(project.root / "logs/project.log"), project.key) as logger:
                logger.info("Project action=%s project_key=%s", action, project.key)
        except (OSError, ProjectError):
            LOGGER.warning("Project metadata committed but audit log unavailable: project_key=%s", project.key)

    @_safe_io
    def create_project(self, project_name: str, description: str = "") -> ProjectSnapshot:
        """Publish a complete skeleton; never overwrite an existing directory."""
        with self._write_lock():
            self._name_available(project_name)
            target = self._project_path(project_name)
            now = datetime.now(timezone.utc).isoformat()
            try:
                metadata = ProjectMetadata(project_name=project_name, description=description, created_at=now, updated_at=now)
            except ValidationError:
                raise ProjectError("INVALID_PROJECT_METADATA", "Project description must be text of at most 4000 characters.") from None
            stage = self._safe_path(self.root / (".creating-" + str(uuid4())))
            stage.mkdir()
            for name in PROJECT_DIRS:
                (stage / name).mkdir()
            write_json(stage / "project.json", metadata.model_dump(mode="json"))
            # Workspace lock serializes our writers. External filesystem manipulation
            # is not a supported concurrency model; recheck immediately before publish.
            if target.exists():
                raise ProjectError("PROJECT_EXISTS", "The project destination is already occupied.")
            stage.rename(target)
            project = self._read(project_name)
            self._event(project, "created")
            return project

    @_safe_io
    def open_project(self, project_key: str) -> ProjectSnapshot:
        """Read disk afresh; no implicit directory repairs or cached session truth."""
        return self._read(project_key)

    @_safe_io
    def list_projects(self) -> list[ProjectSnapshot]:
        self._safe_path(self.root)
        projects = []
        for entry in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            try:
                projects.append(self._read(entry.name))
            except ProjectError as error:
                LOGGER.warning("Project omitted from listing: project_key=%s code=%s", entry.name, error.code)
        return projects

    def _save_change(self, project: ProjectSnapshot, changes: dict[str, Any]) -> ProjectSnapshot:
        data = project.metadata.model_dump(mode="json")
        data.update(changes)
        data["updated_at"] = max(datetime.now(timezone.utc), datetime.fromisoformat(project.metadata.updated_at) + timedelta(microseconds=1)).isoformat()
        try:
            metadata = ProjectMetadata.model_validate(data)
        except ValidationError:
            raise ProjectError("INVALID_PROJECT_METADATA", "Invalid project metadata update.") from None
        write_json(self._safe_path(project.root / "project.json"), metadata.model_dump(mode="json"))
        # No fallible reread after the atomic commit: callers must not report a
        # rollback when the binding was already successfully published.
        return ProjectSnapshot(project.key, project.root, metadata)

    @_safe_io
    def rename_project(self, project_key: str, new_name: str) -> ProjectSnapshot:
        """Rename the DISPLAY name. Immutable key and directories deliberately stay put."""
        with self._write_lock():
            project = self._read(project_key)
            self._name_available(new_name, excluding=project.key)
            updated = self._save_change(project, {"project_name": new_name})
            self._event(updated, "renamed")
            return updated

    @_safe_io
    def update_project_metadata(self, project_key: str, changes: dict[str, Any]) -> ProjectSnapshot:
        """Phase 6.5.1 edits description only; file binding and run state are later APIs."""
        if not isinstance(changes, dict) or not changes or not changes.keys() <= {"description"}:
            raise ProjectError("INVALID_PROJECT_METADATA", "Only description can be updated here; use rename_project for the name.")
        with self._write_lock():
            updated = self._save_change(self._read(project_key), changes)
            self._event(updated, "metadata_updated")
            return updated

    @_safe_io
    def get_project_context(self, project_key: str) -> ProjectContext:
        project = self._read(project_key)
        resolved: dict[str, Path | None] = {}
        for role in FILE_ROLES:
            value = getattr(project.metadata.files, role)
            path = self._safe_path(project.root / value) if value is not None else None
            if path is not None and not path.is_file():
                raise ProjectError("PROJECT_FILE_MISSING", "A registered input file is missing. Re-import or restore it.", details={"role": role})
            resolved[role] = path
        return ProjectContext(project.key, project.metadata.project_name, project.root, project.root / "input",
                              project.root / "results", project.root / "logs", project.root / "reports",
                              project.root / "runtime", **resolved)

    @_safe_io
    def commit_file_binding(self, project_key: str, role: FileRole, relative_path: str | None, *,
                            expected_updated_at: str) -> ProjectSnapshot:
        """Storage-only compare-and-swap; FileImportService owns format validation.

        New bundles are complete before this one atomic metadata update. Concurrent
        project changes cause refusal rather than lost updates or invalid bindings.
        """
        if not isinstance(role, FileRole):
            raise ProjectError("INVALID_FILE_ROLE", "Use a canonical FileRole.")
        return self.commit_file_bindings(project_key, {role: relative_path}, expected_updated_at=expected_updated_at)

    @_safe_io
    def commit_file_bindings(self, project_key: str, bindings: dict[FileRole, str | None], *,
                             expected_updated_at: str) -> ProjectSnapshot:
        """Publish a complete import bundle in one compare-and-swap update."""
        if not bindings or any(not isinstance(role, FileRole) for role in bindings):
            raise ProjectError("INVALID_FILE_ROLE", "Use canonical FileRoles.")
        with self._write_lock():
            project = self._read(project_key)
            if project.metadata.updated_at != expected_updated_at:
                raise ProjectError("PROJECT_CHANGED", "Project changed during validation. Inspect current bindings before retrying.")
            fields = project.metadata.files.model_dump()
            fields.update({role.value: relative for role, relative in bindings.items()})
            from bulk_rnaseq.workspace.models import ProjectFiles
            try:
                validated = ProjectFiles.model_validate(fields)
            except ValidationError:
                raise ProjectError("PROJECT_PATH_NOT_ALLOWED", "Expected a safe project-relative input path.") from None
            for relative_path in bindings.values():
                if relative_path is not None and not self._safe_path(project.root / relative_path).is_file():
                    raise ProjectError("PROJECT_FILE_MISSING", "Cannot bind a missing input file.")
            updated = self._save_change(project, {"files": validated.model_dump()})
            self._event(updated, "file_binding_updated")
            return updated

    @_safe_io
    def delete_project(self, project_key: str, *, confirm: bool = False) -> Path:
        """Recoverable removal only; return the exact recycle directory for recovery."""
        if confirm is not True:
            raise ProjectError("PROJECT_DELETE_CONFIRMATION_REQUIRED", "Confirm moving this project to the workspace recycle directory.")
        with self._write_lock():
            project = self._read(project_key)
            trash = self._safe_path(self.root / ".trash")
            trash.mkdir(exist_ok=True)
            destination = self._safe_path(trash / (project.key + "--" + str(uuid4())))
            self._event(project, "moving_to_recycle")
            # Exact source/destination are validated before moving. No recursive delete.
            self._safe_path(project.root).rename(destination)
            LOGGER.info("Project moved to recycle: project_key=%s recycle_key=%s", project.key, destination.name)
            return destination
