"""Local filesystem relocation only; no Docker, R or live DeepSeek calls."""

import json
import multiprocessing
from pathlib import Path
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from bulk_rnaseq.config import load_runtime_config
from bulk_rnaseq.project_paths import PREFIX, ProjectPaths
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import ProjectError, ProjectManager
from bulk_rnaseq.workspace.chat_store import ChatStore
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.result_resolver import ResultResolver
from bulk_rnaseq.workflow import ProjectHistory, WorkflowExecutor
from bulk_rnaseq.customization.engine import CustomizationEngine
from test_workflow_executor import host, fake_dispatch, deg, volcano, kegg, validate, executor
from test_customization import plan, project as customization_project
from test_file_import import imports


def test_workflow_relocation_and_historical_reuse(host, fake_dispatch, tmp_path):
    manager, registry = host
    first = executor(host).execute(validate(host, deg(), volcano(), kegg()))
    assert first.status == "succeeded"
    source = first.step_results["deg"]["run_id"]
    browser = ResultManager(manager, "demo")
    before = {ref.run_id: ref for ref in browser.list_result_refs()}
    stored = manager.root / "demo/results/workflows" / first.workflow_id / "workflow_result.json"
    raw = stored.read_text(encoding="utf-8")
    assert PREFIX in raw and str(manager.root).replace("\\", "\\\\") not in raw
    assert before[first.step_results["kegg"]["run_id"]].parent_run_ids == [source]
    checksums = {p.relative_to(manager.root): sha256(p) for p in manager.root.rglob("*.json")}
    destination = tmp_path / "new_workspace"
    shutil.copytree(manager.root, destination)
    # Exact pytest-owned directory, rename rather than delete; old paths cannot work.
    assert manager.root.resolve().is_relative_to(tmp_path.resolve())
    manager.root.rename(tmp_path / "offline_old_workspace")
    moved = ProjectManager(destination)
    assert all(sha256(destination / p) == checksum for p, checksum in checksums.items())
    context = moved.get_project_context("demo")
    assert context.count_matrix.is_file() and context.count_matrix.is_relative_to(destination)
    selected = ResultResolver(moved, "demo").find_compatible_result(
        skill_name="rnaseq.deg", run_id=source, required_outputs=("deg_csv", "filtered_csv"))
    assert selected.status == "resolved"
    after = ResultManager(moved, "demo").list_result_refs()
    assert {r.run_id: r.parent_run_ids for r in after} == {k: v.parent_run_ids for k, v in before.items()}
    artifact, checksum = ProjectHistory(moved, "demo").artifact(source, "deg_csv")
    assert artifact.is_relative_to(destination) and sha256(artifact) == checksum
    step = volcano()
    step["input_bindings"]["deg_result_file"] = {"source": "project_result", "run_id": source, "output": "deg_csv"}
    fake_dispatch[0].clear()
    checked = validate((moved, registry), step)
    second = WorkflowExecutor(registry, moved, "demo").execute(checked)
    assert second.status == "succeeded"
    assert [name for name, _ in fake_dispatch[0]] == ["rnaseq.volcano"]


def test_customization_survives_relocation(customization_project, tmp_path):
    manager, snapshot, table, annotation = customization_project
    from bulk_rnaseq.builtin_registry import create_default_registry
    registry = create_default_registry()
    options = {"keywords": ["sex"], "annotation_sources": ["gene_description"], "match_mode": "any"}
    # Real deterministic Python annotation filtering, no R/model invocation.
    result = CustomizationEngine(manager, snapshot.key, registry).execute(
        plan("annotation_filter", "rnaseq.deg", options), source_run_id="source-run", annotation_files=[annotation])
    assert result["status"] == "succeeded", result
    destination = tmp_path / "copied_customization"
    shutil.copytree(manager.root, destination)
    assert manager.root.resolve().is_relative_to(tmp_path.resolve())
    manager.root.rename(tmp_path / "offline_customization")
    moved = ProjectManager(destination)
    browser = ResultManager(moved, snapshot.key)
    records = browser.list_collections("Customization")
    assert records[0]["source_run_id"] == "source-run"
    report = browser._json(browser.root / records[0]["source"])
    assert report["source_run_id"] == "source-run"
    assert all(Path(binding["source_path"]).is_relative_to(destination) for binding in report["input_bindings"].values())
    output, checksum = ProjectHistory(moved, snapshot.key).artifact(result["skill_result"]["run_id"], "filtered_table")
    assert sha256(output) == checksum


@pytest.mark.parametrize("value", ["../Other/results/x.csv", "results/../input/x", "results//x", "results/./x",
    "results\\x", "/outside/x", "C:\\old_workspace\\demo\\results\\x", "results/CON.csv",
    "project-path:v1:Other:results/x", "project-path:v1:demo:../Other/x",
    "project-path:v1:demo:/etc/passwd", "project-path:v1:demo:C:\\outside.csv"])
def test_path_boundaries(tmp_path, value):
    manager = ProjectManager(tmp_path / "workspace")
    manager.create_project("demo")
    with pytest.raises(ProjectError):
        manager.get_project_context("demo").resolve_path(value)


def test_native_and_portable_paths_roundtrip_and_text_preserved(tmp_path):
    manager = ProjectManager(tmp_path / "workspace")
    snapshot = manager.create_project("demo")
    context = manager.get_project_context("demo")
    target = snapshot.root / "results" / "x.csv"
    target.write_text("fixture")
    reference = context.portable_path(target)
    assert reference == PREFIX + "demo:results/x.csv"
    assert context.resolve_path(reference, must_exist=True) == target
    assert context.resolve_path(target) == target
    path = snapshot.root / "runtime" / "fixture.json"
    payload = {"path": str(target), "user_request": str(target), "description": reference}
    write_json(path, payload)
    raw = json.loads(path.read_text())
    assert raw["path"] == reference and raw["user_request"] == str(target)
    assert context.restore_paths(raw) == payload


def test_cross_project_reference_and_link_rejected(tmp_path, monkeypatch):
    manager = ProjectManager(tmp_path / "workspace")
    a, b = manager.create_project("A"), manager.create_project("B")
    context = manager.get_project_context("A")
    with pytest.raises(ProjectError):
        context.resolve_path(b.root / "results/x.csv")
    target = a.root / "results/x.csv"
    monkeypatch.setattr(Path, "is_junction", lambda p: p == a.root / "results")
    with pytest.raises(ProjectError):
        context.resolve_path(target)


def test_legacy_absolute_is_not_remapped(tmp_path):
    manager = ProjectManager(tmp_path / "workspace")
    project = manager.create_project("demo")
    target = project.root / "results" / "legacy.csv"
    target.write_text("fixture")
    browser = ResultManager(manager, "demo")
    assert browser.validate_result_path(str(target)) == target
    wrong = tmp_path / "old_workspace" / "demo/results/legacy.csv"
    with pytest.raises(ProjectError):
        browser.validate_result_path(str(wrong))
    assert browser._artifact("table", wrong).error_code == "RESULT_PATH_NOT_ALLOWED"


def _hold_workspace(root, ready, release):
    manager = ProjectManager(Path(root))
    with manager._write_lock():
        ready.set()
        if not release.wait(15):
            raise RuntimeError("Test lock timed out")


def test_cross_process_metadata_and_index_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("bulk_rnaseq.workspace.manager.LOCK_WAIT_SECONDS", 0.25)
    manager = ProjectManager(tmp_path / "workspace")
    project = manager.create_project("demo")
    original = (project.root / "project.json").read_bytes()
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_hold_workspace, args=(str(manager.root), ready, release))
    process.start()
    try:
        assert ready.wait(15)
        assert ResultManager(manager, "demo").list_result_refs() == []
        for action in (lambda: manager.update_project_metadata("demo", {"description": "conflicting write"}),
                       lambda: ChatStore(manager, "demo").append([])):
            with pytest.raises(ProjectError) as caught:
                action()
            assert caught.value.code == "WORKSPACE_BUSY"
        assert (project.root / "project.json").read_bytes() == original
    finally:
        release.set()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join()
    assert process.exitcode == 0
    manager.update_project_metadata("demo", {"description": "unlocked"})
    assert manager.open_project("demo").metadata.description == "unlocked"


def test_chat_concurrent_writers_preserve_every_accepted_message(tmp_path):
    manager = ProjectManager(tmp_path / "workspace")
    manager.create_project("demo")
    def append(index):
        row = {"role": "user", "request_id": str(index), "content": "fixture", "timestamp": "fixture", "status": "queued"}
        try:
            ChatStore(manager, "demo").append([row])
            return str(index)
        except ProjectError as error:
            assert error.code == "WORKSPACE_BUSY"
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = set(pool.map(append, range(12))) - {None}
    assert accepted
    assert {r["request_id"] for r in ChatStore(manager, "demo").history()} == accepted


def test_atomic_fsync_failure_keeps_old_document(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    write_json(path, {"old": True})
    def denied(_):
        raise PermissionError("fixture write denied")
    monkeypatch.setattr("bulk_rnaseq.run_io.os.fsync", denied)
    with pytest.raises(PermissionError):
        write_json(path, {"old": False})
    assert json.loads(path.read_text()) == {"old": True}
    assert not list(tmp_path.glob(".json-*.tmp"))


def test_runtime_config_shared_and_no_secret(tmp_path, monkeypatch):
    env = {"RNASEQ_WORKSPACE_ROOT": str(tmp_path / "volume"), "RNASEQ_RSCRIPT": "custom-Rscript",
           "UVICORN_PORT": "8010", "STREAMLIT_SERVER_PORT": "8510", "DEEPSEEK_API_KEY": "synthetic-private-value"}
    settings = load_runtime_config(environ=env)
    assert settings.workspace_root == tmp_path / "volume"
    assert settings.api_port == 8010 and settings.streamlit_port == 8510
    assert "synthetic-private-value" not in repr(settings)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from bulk_rnaseq.api.main import create_app
    app = create_app()
    assert app.state.workspace_root == settings.workspace_root
    assert app.state.rscript_executable == settings.rscript_executable


@pytest.mark.parametrize("setting,value", [("UVICORN_PORT", "0"), ("STREAMLIT_SERVER_PORT", "65536"),
    ("RNASEQ_LOG_LEVEL", "nonsense"), ("RNASEQ_WORKSPACE_ROOT", ""), ("RNASEQ_RSCRIPT", "\ncommand")])
def test_bad_runtime_config(setting, value):
    with pytest.raises(ValueError):
        load_runtime_config(environ={setting: value})


def test_import_bindings_readiness_and_chat_restore(imports, tmp_path):
    from bulk_rnaseq.workspace import FileImportService
    service, files = imports
    for role, path in files.items():
        assert service.import_file("Test", path, role).status == "succeeded"
    old = service.manager.get_project_context("Test")
    store = ChatStore(service.manager, "Test")
    row = {"role": "assistant", "request_id": "fixture", "content": "fixture", "timestamp": "fixture",
           "status": "succeeded", "result": {"artifacts": [{"name": "input", "path": str(old.count_matrix)}]}}
    store.append([row])
    (old.reports_root / "fixture.txt").write_text("persistent report")
    destination = tmp_path / "new_import_root"
    shutil.copytree(service.manager.root, destination)
    service.manager.root.rename(tmp_path / "old_import_offline")
    moved = FileImportService(ProjectManager(destination))
    for role in files:
        info = moved.get_imported_file_info("Test", role)
        assert info["status"] in {"valid", "warning"}
    readiness = moved.get_project_readiness("Test")
    assert readiness["rnaseq.pca"]["ready"]
    assert readiness["rnaseq.deg"]["data_ready"]
    restored = ChatStore(moved.manager, "Test").history()[0]
    assert Path(restored["result"]["artifacts"][0]["path"]).is_relative_to(destination)
    assert moved.manager.get_project_context("Test").reports_root.joinpath("fixture.txt").read_text() == "persistent report"


def test_index_contention_does_not_abort_completed_skill(host, fake_dispatch, monkeypatch):
    original = ResultManager.register_result
    def busy(*args, **kwargs):
        raise ProjectError("WORKSPACE_BUSY", "Fixture contention")
    monkeypatch.setattr(ResultManager, "register_result", busy)
    result = executor(host).execute(validate(host, deg(), volcano()))
    assert result.status == "succeeded"
    assert any("RESULT_INDEX_BUSY" in warning for warning in result.warnings)
    monkeypatch.setattr(ResultManager, "register_result", original)
    refs = ResultManager(host[0], "demo").list_result_refs()
    assert len(refs) == 2
    child = next(ref for ref in refs if ref.skill_name == "rnaseq.volcano")
    assert child.parent_run_ids == [result.step_results["deg"]["run_id"]]


def test_history_contention_is_not_reported_as_missing_dependency(host, fake_dispatch):
    from bulk_rnaseq.exceptions import SkillError
    from bulk_rnaseq.workflow.history import ProjectHistory
    result = executor(host).execute(validate(host, deg()))
    manager = host[0]
    identity = result.step_results["deg"]["run_id"]
    with manager._write_lock(), pytest.raises(SkillError) as caught:
        ProjectHistory(manager, "demo").artifact(identity, "deg_csv")
    assert caught.value.code == "WORKSPACE_BUSY"
    # Contention does not damage or invalidate the actual source.
    path, checksum = ProjectHistory(manager, "demo").artifact(identity, "deg_csv")
    assert path.is_file() and checksum


def test_result_browsing_does_not_require_missing_inputs(host, fake_dispatch):
    first = executor(host).execute(validate(host, deg()))
    manager = host[0]
    context = manager.get_project_context("demo")
    context.metadata.rename(context.metadata.with_suffix(".offline"))
    with pytest.raises(ProjectError, match="registered input"):
        manager.get_project_context("demo")
    browser = ResultManager(manager, "demo")
    assert browser.get_run(first.step_results["deg"]["run_id"]).status == "succeeded"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only desktop launcher; not a Linux service entry point")
def test_desktop_launcher_uses_shared_workspace_and_port(tmp_path, monkeypatch):
    from unittest.mock import MagicMock, Mock
    from app.local_server import LocalServer
    root = tmp_path / "application"
    python = root / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"not executable; mocked launcher only")
    entry = root / "app/main.py"
    entry.parent.mkdir()
    entry.write_text("# fixture")
    volume = tmp_path / "volume"
    monkeypatch.setenv("RNASEQ_WORKSPACE_ROOT", str(volume))
    monkeypatch.setenv("STREAMLIT_SERVER_PORT", "8520")
    monkeypatch.setattr("app.local_server.socket.socket", MagicMock())
    spawn = Mock()
    monkeypatch.setattr("app.local_server.subprocess.Popen", spawn)
    server = LocalServer(root)
    server.start()
    try:
        assert server.port == 8520
        assert server.log_path.is_relative_to(volume)
        assert "--server.address=127.0.0.1" in spawn.call_args.args[0]
        assert spawn.call_args.kwargs["env"]["RNASEQ_WORKSPACE_ROOT"] == str(volume)
    finally:
        server.stop()


def test_project_root_retarget_is_rejected(tmp_path, monkeypatch):
    manager = ProjectManager(tmp_path / "workspace")
    manager.create_project("demo")
    context = manager.get_project_context("demo")
    original = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda p, **kw: tmp_path / "outside" if p == context.project_root else original(p, **kw))
    with pytest.raises(ProjectError, match="root has changed"):
        context.resolve_path("results/table.csv")


def test_customization_uses_context_resolver_for_legacy_paths(customization_project):
    from bulk_rnaseq.builtin_registry import create_default_registry
    manager, snapshot, table, annotation = customization_project
    engine = CustomizationEngine(manager, snapshot.key, create_default_registry())
    assert engine._input_path(table) == table
    with pytest.raises(ProjectError):
        engine._input_path(Path("Z:/unavailable_legacy_workspace/custom_test/input/metadata.csv"))
