"""Thin transport/read-only helpers. No Streamlit dependency or analysis logic."""

from pathlib import Path
import os
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO

from bulk_rnaseq.workspace import FileImportService, FileRole, ImportResult, ProjectError, ProjectManager
from bulk_rnaseq.workspace.file_import import failure
from bulk_rnaseq.workspace.models import validate_name, validate_relative_input


MAX_UPLOAD_BYTES = 300 * 1024 * 1024


def import_upload(service: FileImportService, project_key: str, filename: str, stream: BinaryIO,
                  role: FileRole | str, *, replace: bool = False, **options: Any) -> ImportResult:
    """Copy a binary upload to a private temporary file; delegate all validation.

    The filename is validated BEFORE Path construction, including both separator
    styles. A browser never gets to select the temporary destination directory.
    """
    action = "replace" if replace else "import"
    try:
        try:
            validate_name(filename)
        except ValueError:
            raise ProjectError("UNSAFE_FILENAME", "Choose a plain, portable filename without path separators.") from None
        project = service.manager.open_project(project_key)
        runtime = service.manager._safe_path(project.root / "runtime")
        with TemporaryDirectory(prefix="upload-", dir=runtime) as temporary:
            source = Path(temporary) / filename
            stream.seek(0)
            size = 0
            with source.open("wb") as target:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise ProjectError("UPLOAD_TOO_LARGE", "This version accepts files up to 300 MiB.")
                    target.write(chunk)
            if role == "eggnog":
                return service.import_eggnog(project_key, source, replace=replace, **options)
            return service.import_file(project_key, source, role, replace=replace, **options)
    except (ProjectError, OSError, ValueError, TypeError) as error:
        result = failure(action, role, error, filename)
        service._audit(project_key, result)
        return result


def describe_download(service: FileImportService, project_key: str, role: FileRole | str) -> dict[str, Any]:
    """Describe the immutable ORIGINAL upload; never trust a browser-supplied path."""
    project = service.manager.open_project(project_key)
    record = service._record(project, FileRole(role))
    if record is None:
        raise ProjectError("FILE_ROLE_NOT_BOUND", "No file is imported for this role.")
    original = service.manager._safe_path(project.root / validate_relative_input(record["original_path"]))
    return {"filename": record["original_filename"], "size_bytes": original.stat().st_size,
            "stored_path": record["stored_path"], "original_path": record["original_path"]}


def read_download(service: FileImportService, project_key: str, role: FileRole | str,
                  *, expected_binding: str) -> tuple[str, bytes]:
    """Recheck current binding/integrity before returning any bytes to the UI."""
    info = describe_download(service, project_key, role)
    if info["stored_path"] != expected_binding:
        raise ProjectError("PROJECT_CHANGED", "The imported file changed. Refresh before downloading.")
    if info["size_bytes"] > MAX_UPLOAD_BYTES:
        raise ProjectError("DOWNLOAD_TOO_LARGE", "This basic UI downloads files up to 300 MiB.")
    project = service.manager.open_project(project_key)
    path = service.manager._safe_path(project.root / info["original_path"])
    with path.open("rb") as handle:
        data = handle.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ProjectError("DOWNLOAD_TOO_LARGE", "File exceeds the basic UI download limit.")
    return info["filename"], data


def read_mapping_download(service: FileImportService, project_key: str, role: FileRole | str,
                          *, expected_binding: str) -> tuple[str, bytes]:
    """Download the verified active mapping, not the original eggNOG upload."""
    role = FileRole(role)
    if role not in {FileRole.GO_MAPPING, FileRole.KEGG_MAPPING}:
        raise ProjectError("INVALID_FILE_ROLE", "Select a GO or KEGG mapping.")
    project = service.manager.open_project(project_key)
    record = service._record(project, role)
    if record is None or record["stored_path"] != expected_binding:
        raise ProjectError("PROJECT_CHANGED", "The mapping changed. Refresh before downloading.")
    path = service.manager._safe_path(project.root / validate_relative_input(record["stored_path"]))
    with path.open("rb") as handle:
        data = handle.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ProjectError("DOWNLOAD_TOO_LARGE", "File exceeds the basic UI download limit.")
    return path.name, data


def list_project_results(manager: ProjectManager, project_key: str, *, max_entries: int = 10000) -> list[dict[str, Any]]:
    """Bounded metadata-only traversal of results/. Never follows linked paths.

    Folder names are shown as-is; existence never implies a successful analysis.
    No table/image contents are opened and no historical results are rewritten.
    """
    project = manager.open_project(project_key)
    root = manager._safe_path(project.root / "results")
    if type(max_entries) is not int or max_entries < 1:
        raise ProjectError("INVALID_PARAMETER", "max_entries must be positive.")
    rows: dict[str, dict[str, Any]] = {}
    pending = [root]
    scanned = 0
    truncated = False
    while pending and not truncated:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                scanned += 1
                if scanned > max_entries:
                    truncated = True
                    break
                path = Path(entry.path)
                relative = path.relative_to(root)
                name = relative.parts[0] if len(relative.parts) > 1 or entry.is_dir(follow_symlinks=False) else "."
                row = rows.setdefault(name, {"directory": name, "file_count": 0, "summary_exists": False,
                                             "linked_entries_skipped": 0, "truncated": False})
                if path.is_symlink() or path.is_junction():
                    row["linked_entries_skipped"] += 1
                    continue
                manager._safe_path(path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    row["file_count"] += 1
                    row["summary_exists"] |= path.name == "summary.json"
    if truncated:
        for row in rows.values():
            row["truncated"] = True
    return [rows[name] for name in sorted(rows)]


def format_file_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("Unreachable")
