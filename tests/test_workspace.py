"""Project persistence/security tests; temporary user projects, no R or network."""

import json
import logging
from pathlib import Path
import shutil
from unittest.mock import Mock

import pytest

from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workspace import ProjectError, ProjectManager
from bulk_rnaseq.workspace.models import PROJECT_DIRS


@pytest.fixture
def manager(tmp_path: Path) -> ProjectManager:
    return ProjectManager(tmp_path / "workspace")


def test_create_layout_and_portable_metadata(manager: ProjectManager) -> None:
    project = manager.create_project("Black_porgy", "真实 RNA-seq 项目")
    assert project.key == "Black_porgy" and project.root == manager.root / project.key
    assert all((project.root / name).is_dir() for name in PROJECT_DIRS)
    metadata = json.loads((project.root / "project.json").read_text(encoding="utf-8"))
    assert set(metadata) == {"schema_version", "project_name", "created_at", "updated_at", "description", "files", "analysis", "last_run"}
    assert metadata["files"] == {"count_matrix": None, "metadata": None, "go_mapping": None, "kegg_mapping": None}
    assert metadata["analysis"] == {} and metadata["last_run"] is None
    assert metadata["created_at"] == metadata["updated_at"]
    assert str(manager.root) not in json.dumps(metadata)
    assert "created" in (project.root / "logs/project.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["Black_porgy", "black_PORGY"])
def test_duplicate_never_overwrites(manager: ProjectManager, name: str) -> None:
    project = manager.create_project("Black_porgy")
    before = (project.root / "project.json").read_bytes()
    with pytest.raises(ProjectError, match="already exists") as caught:
        manager.create_project(name)
    assert caught.value.code == "PROJECT_EXISTS"
    assert (project.root / "project.json").read_bytes() == before


@pytest.mark.parametrize("name", ["", ".", "..", "../outside", "a/b", "a\\b", "C:\\data", "C:data", "/tmp",
                                  "a:b", "a*", "a?", "a|", "a<", 'a"', "a\n", "a\x00", " a", "a ", "a.",
                                  ".trash", ".env", "CON", "con.txt", "NUL", "COM1", "LPT9.csv", "COM¹", "x" * 81, None])
def test_invalid_name(manager: ProjectManager, name: str) -> None:
    with pytest.raises(ProjectError) as caught:
        manager.create_project(name)
    assert caught.value.code == "INVALID_PROJECT_NAME"
    assert not manager.list_projects() and not (manager.root / ".workspace.lock").exists()


def test_unicode_project_names(manager: ProjectManager) -> None:
    assert manager.create_project("黑鲷 RNA-seq").metadata.project_name == "黑鲷 RNA-seq"


def test_open_list_and_reopen_from_disk(manager: ProjectManager) -> None:
    first = manager.create_project("Tilapia")
    manager.create_project("Black_porgy")
    other = ProjectManager(manager.root)
    assert [p.key for p in other.list_projects()] == ["Black_porgy", "Tilapia"]
    assert other.open_project(first.key).metadata == first.metadata


def test_missing_project(manager: ProjectManager) -> None:
    with pytest.raises(ProjectError) as caught:
        manager.open_project("absent")
    assert caught.value.code == "PROJECT_NOT_FOUND"


def test_rename_display_only_preserves_folder_inputs_and_created_time(manager: ProjectManager) -> None:
    project = manager.create_project("Original")
    sentinel = project.root / "input/data.txt"
    sentinel.write_text("user data", encoding="utf-8")
    renamed = manager.rename_project(project.key, "New Name")
    assert renamed.key == project.key and renamed.root == project.root
    assert renamed.metadata.project_name == "New Name" and not (manager.root / "New Name").exists()
    assert renamed.metadata.created_at == project.metadata.created_at
    assert renamed.metadata.updated_at >= project.metadata.updated_at
    assert sentinel.read_text(encoding="utf-8") == "user data"
    assert ProjectManager(manager.root).open_project(project.key).metadata.project_name == "New Name"


def test_rename_and_create_duplicate_display_name(manager: ProjectManager) -> None:
    manager.create_project("first")
    manager.create_project("second")
    manager.rename_project("first", "new_name")
    for action in (lambda: manager.rename_project("second", "NEW_NAME"), lambda: manager.create_project("new_name")):
        with pytest.raises(ProjectError) as caught:
            action()
        assert caught.value.code == "PROJECT_EXISTS"


def test_update_metadata_and_no_stale_cache(manager: ProjectManager) -> None:
    project = manager.create_project("Example")
    updated = manager.update_project_metadata(project.key, {"description": "updated"})
    assert updated.metadata.description == "updated" and project.metadata.description == ""
    assert ProjectManager(manager.root).open_project(project.key).metadata.description == "updated"


@pytest.mark.parametrize("changes", [{"project_name": "other"}, {"created_at": "bad"}, {"files": {}},
                                      {"analysis": {}}, {"last_run": "fake"}, {"api_key": "secret"},
                                      {"description": 42}, {"description": "x" * 4001}, {}, None])
def test_unsupported_metadata_updates_preserve_disk(manager: ProjectManager, changes: dict) -> None:
    project = manager.create_project("Example")
    before = (project.root / "project.json").read_bytes()
    with pytest.raises(ProjectError) as caught:
        manager.update_project_metadata(project.key, changes)
    assert caught.value.code == "INVALID_PROJECT_METADATA"
    assert (project.root / "project.json").read_bytes() == before


def test_new_context_missing_inputs_is_explicit(manager: ProjectManager) -> None:
    manager.create_project("Example")
    context = manager.get_project_context("Example")
    assert context.project_root == manager.root / "Example"
    assert context.output_root == context.project_root / "results"
    assert context.count_matrix is None and context.metadata is None
    assert context.go_mapping is None and context.kegg_mapping is None


def seed_file_reference(project_root: Path, relative: str = "input/counts.csv") -> None:
    """Persisted-metadata fixture only, NOT a production import implementation."""
    path = project_root / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"]["count_matrix"] = relative
    write_json(path, data)


def test_move_workspace_regenerates_paths_without_editing_metadata(manager: ProjectManager, tmp_path: Path) -> None:
    project = manager.create_project("Example")
    (project.root / "input/counts.csv").write_text("fixture only", encoding="utf-8")
    seed_file_reference(project.root)
    before = (project.root / "project.json").read_bytes()
    destination = tmp_path / "relocated_workspace"
    shutil.copytree(manager.root, destination)
    relocated = ProjectManager(destination)
    context = relocated.get_project_context("Example")
    assert context.count_matrix == destination / "Example/input/counts.csv"
    assert context.output_root == destination / "Example/results"
    assert (destination / "Example/project.json").read_bytes() == before


def test_registered_missing_file(manager: ProjectManager) -> None:
    project = manager.create_project("Example")
    seed_file_reference(project.root)
    with pytest.raises(ProjectError) as caught:
        manager.get_project_context(project.key)
    assert caught.value.code == "PROJECT_FILE_MISSING"
    assert caught.value.details == {"role": "count_matrix"}


@pytest.mark.parametrize("relative", ["../secret.csv", "input/../../secret.csv", "input/./counts.csv", "input//counts.csv",
                                      "C:/data.csv", "C:data.csv", "/tmp/data.csv", "\\\\host\\share\\a.csv",
                                      "input\\counts.csv", "results/counts.csv", "input/.env", "input/CON.csv"])
def test_unsafe_persisted_paths(manager: ProjectManager, relative: str) -> None:
    project = manager.create_project("Example")
    seed_file_reference(project.root, relative)
    with pytest.raises(ProjectError) as caught:
        manager.open_project(project.key)
    assert caught.value.code == "PROJECT_METADATA_INVALID"


def test_corrupt_metadata_does_not_repair_or_hide_other_projects(manager: ProjectManager, caplog: pytest.LogCaptureFixture) -> None:
    bad = manager.create_project("Bad")
    manager.create_project("Good")
    path = bad.root / "project.json"
    path.write_text('{"bad":', encoding="utf-8")
    with pytest.raises(ProjectError) as caught:
        manager.open_project("Bad")
    assert caught.value.code == "PROJECT_METADATA_INVALID"
    with caplog.at_level(logging.WARNING):
        assert [p.key for p in manager.list_projects()] == ["Good"]
    assert "Bad" in caplog.text and "PROJECT_METADATA_INVALID" in caplog.text
    assert path.read_text(encoding="utf-8") == '{"bad":'


@pytest.mark.parametrize("mode", ["duplicate_key", "wrong_version", "missing_fields", "oversize", "invalid_utf8", "bad_timestamp"])
def test_invalid_metadata_variants(manager: ProjectManager, mode: str) -> None:
    project = manager.create_project("Example")
    path = project.root / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if mode == "duplicate_key":
        path.write_text(path.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'), encoding="utf-8")
    elif mode == "oversize":
        path.write_text("x" * 65537, encoding="utf-8")
    elif mode == "invalid_utf8":
        path.write_bytes(b"\xff\xfe")
    else:
        if mode == "wrong_version":
            data["schema_version"] = 99
        elif mode == "missing_fields":
            del data["files"]
        else:
            data["created_at"] = "2026-01-01"
        write_json(path, data)
    with pytest.raises(ProjectError) as caught:
        manager.open_project(project.key)
    assert caught.value.code == "PROJECT_METADATA_INVALID"


def test_missing_layout_is_not_silently_recreated(manager: ProjectManager) -> None:
    project = manager.create_project("Example")
    (project.root / "runtime").rmdir()
    with pytest.raises(ProjectError) as caught:
        manager.open_project(project.key)
    assert caught.value.code == "PROJECT_LAYOUT_INVALID" and not (project.root / "runtime").exists()


@pytest.mark.parametrize("confirm", [False, None, 1, "yes"])
def test_delete_requires_exact_confirmation(manager: ProjectManager, confirm: bool) -> None:
    project = manager.create_project("Example")
    with pytest.raises(ProjectError) as caught:
        manager.delete_project(project.key, confirm=confirm)
    assert caught.value.code == "PROJECT_DELETE_CONFIRMATION_REQUIRED" and project.root.is_dir()


def test_delete_moves_to_recoverable_location(manager: ProjectManager) -> None:
    project = manager.create_project("Example")
    sentinel = project.root / "input/original.txt"
    sentinel.write_text("precious fixture", encoding="utf-8")
    metadata = (project.root / "project.json").read_bytes()
    recovery = manager.delete_project(project.key, confirm=True)
    assert recovery.parent == manager.root / ".trash" and not project.root.exists()
    assert (recovery / "input/original.txt").read_text(encoding="utf-8") == "precious fixture"
    assert (recovery / "project.json").read_bytes() == metadata and manager.list_projects() == []
    # Manual recovery rehearsal of this exact temporary test directory.
    assert recovery.resolve().is_relative_to(manager.root.resolve())
    recovery.rename(project.root)
    assert manager.open_project(project.key).key == project.key


def test_workspace_lock_blocks_writers(manager: ProjectManager) -> None:
    lock = manager.root / ".workspace.lock"
    lock.write_text("fixture existing writer", encoding="utf-8")
    with pytest.raises(ProjectError) as caught:
        manager.create_project("Example")
    assert caught.value.code == "WORKSPACE_BUSY" and lock.read_text() == "fixture existing writer"


def test_atomic_update_failure_keeps_previous_json(manager: ProjectManager, monkeypatch: pytest.MonkeyPatch) -> None:
    project = manager.create_project("Example")
    before = (project.root / "project.json").read_bytes()
    original = Path.replace
    def denied(path: Path, target: Path) -> Path:
        if Path(target).name == "project.json":
            raise PermissionError("sensitive OS detail")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(ProjectError) as caught:
        manager.rename_project(project.key, "Changed")
    assert caught.value.code == "PROJECT_IO_ERROR" and "sensitive" not in str(caught.value)
    assert (project.root / "project.json").read_bytes() == before
    assert not (manager.root / ".workspace.lock").exists()
    assert not list(project.root.glob(".json-*.tmp"))


def test_create_failure_does_not_publish_partial_project(manager: ProjectManager, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bulk_rnaseq.workspace.manager.write_json", Mock(side_effect=OSError("fixture failure")))
    with pytest.raises(ProjectError) as caught:
        manager.create_project("Example")
    assert caught.value.code == "PROJECT_IO_ERROR" and not (manager.root / "Example").exists()
    assert manager.list_projects() == [] and not (manager.root / ".workspace.lock").exists()


def test_error_contract_does_not_masquerade_as_skill_result() -> None:
    error = ProjectError("PROJECT_NOT_FOUND", "Project not found.")
    assert error.to_dict() == {"status": "failed", "error_code": "PROJECT_NOT_FOUND", "message": "Project not found.", "details": {}}


def test_linked_input_rejected(manager: ProjectManager, tmp_path: Path) -> None:
    project = manager.create_project("Example")
    target = tmp_path / "outside.csv"
    target.write_text("outside", encoding="utf-8")
    link = project.root / "input/link.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Creating Windows symlinks requires local permission; junction guard is tested separately.")
    seed_file_reference(project.root, "input/link.csv")
    with pytest.raises(ProjectError) as caught:
        manager.get_project_context(project.key)
    assert caught.value.code == "PROJECT_PATH_NOT_ALLOWED" and target.read_text() == "outside"


@pytest.mark.parametrize("linked", ["input", "logs", "project.json"])
def test_junction_guard_is_portable(manager: ProjectManager, monkeypatch: pytest.MonkeyPatch, linked: str) -> None:
    project = manager.create_project("Example")
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == project.root / linked or original(path))
    with pytest.raises(ProjectError) as caught:
        manager.delete_project(project.key, confirm=True)
    assert caught.value.code == "PROJECT_PATH_NOT_ALLOWED" and project.root.exists()
