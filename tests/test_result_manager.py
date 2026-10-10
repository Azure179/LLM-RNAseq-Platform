"""Read-only result tests. All synthetic records below are explicit test fixtures."""

from io import BytesIO
import json
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest
from streamlit.testing.v1 import AppTest

from app.state import STATE_KEY, set_current_project, widget_key
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import ProjectManager, ProjectError, FileImportService
from bulk_rnaseq.workspace.chat_store import ChatStore, now
from bulk_rnaseq.workspace.result_manager import ResultManager, display_value
from bulk_rnaseq.workspace.result_preview import preview_table
from test_workspace_ui import APP, apptest_ipc, element


def record(root, run_id="fixture-deg", skill="rnaseq.deg", *, failed=False, empty=False):
    directory = root / run_id
    directory.mkdir(parents=True)
    table = directory / "table.csv"
    table.write_text("gene_id,log2FoldChange,padj\n" + "".join(f"g{i},{2 if i % 2 else -2},0.01\n" for i in range(1200)), encoding="utf-8")
    plot = directory / "figure.png"
    Image.new("RGB", (100, 100), "white").save(plot)
    summary = {"genes_analyzed": 1200, "upregulated_genes": 600, "downregulated_genes": 600} if skill == "rnaseq.deg" else {"n_enriched": 0 if empty else 2}
    result = SkillResult("failed" if failed else "succeeded", skill, run_id,
        inputs={"parameters": {"treatment": "OT", "control": "T", "alpha": .05}},
        outputs={} if failed else {"deg_csv": str(table), "filtered_csv": str(table), "plot": str(plot), "summary_json": str(directory / "summary.json")},
        summary={} if failed else summary,
        warnings=[] if failed else [{"code": "FIXTURE_WARNING", "message": "Fixture warning", "details": {}}],
        metadata={"started_at": "2026-09-01T10:00:00+00:00"},
        error_code="FIXTURE_FAILURE" if failed else None, message="Fixture failure" if failed else None)
    write_json(directory / "summary.json", result.to_dict())
    return result


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr("bulk_rnaseq.registry.SkillRegistry.execute", Mock(side_effect=AssertionError("Read-only UI cannot execute Skills")))
    monkeypatch.setattr("subprocess.run", Mock(side_effect=AssertionError("No R")))
    projects = ProjectManager(tmp_path / "workspace")
    projects.create_project("First")
    projects.create_project("Second")
    record(projects.root / "First/results")
    record(projects.root / "First/results", "fixture-empty", "rnaseq.kegg", empty=True)
    record(projects.root / "First/results", "fixture-failed", "rnaseq.go", failed=True)
    return ResultManager(projects, "First")


def test_list_filter_lookup_and_no_mutation(manager):
    source = manager.root / "fixture-deg/summary.json"
    before = sha256(source)
    assert len(manager.list_runs()) == 3
    assert len(manager.list_skill_runs("rnaseq.deg")) == 1
    assert len(manager.list_runs(status="failed")) == 1
    assert len(manager.list_runs(status="warning")) == 2
    assert manager.get_run("fixture-deg").comparison == "OT vs T"
    assert manager.get_run_summary("fixture-empty")["n_enriched"] == 0
    assert manager.get_run("fixture-empty").status == "succeeded"
    assert manager.get_run("fixture-failed").error_code == "FIXTURE_FAILURE"
    assert manager.get_run("fixture-deg").created_at.startswith("2026-09-01")
    assert manager.list_runs(newest_first=False) == list(reversed(manager.list_runs()))
    assert sha256(source) == before


def test_missing_and_cross_project(manager):
    with pytest.raises(ProjectError, match="唯一"):
        manager.get_run("missing")
    assert ResultManager(manager.manager, "Second").list_runs() == []
    with pytest.raises(ProjectError):
        ResultManager(manager.manager, "Second").get_run("fixture-deg")


@pytest.mark.parametrize("relative", ["../project.json", "../../Second/project.json", "fixture-deg/../../../Second/project.json"])
def test_path_traversal(manager, relative):
    with pytest.raises(ProjectError):
        manager.validate_result_path(relative)


def test_absolute_other_project_and_forged_artifact(manager):
    path = manager.root / "fixture-deg/summary.json"
    payload = json.loads(path.read_text())
    payload["outputs"]["evil"] = str(manager.manager.root / "Second/project.json")
    write_json(path, payload)
    run = manager.get_run("fixture-deg")
    assert next(a for a in run.artifacts if a.artifact_id == "evil").error_code == "RESULT_PATH_NOT_ALLOWED"
    with pytest.raises(ProjectError):
        manager.download(run.run_id, "evil")


def test_missing_artifact_does_not_hide_run(manager):
    manager.list_runs()
    (manager.root / "fixture-deg/table.csv").unlink()
    with pytest.raises(ProjectError) as error:
        manager.download("fixture-deg", "deg_csv")
    assert error.value.code == "ARTIFACT_MISSING"
    manager.refresh()
    assert manager.get_run("fixture-deg").status == "succeeded"
    assert next(a for a in manager.list_artifacts("fixture-deg") if a.artifact_id == "deg_csv").error_code == "ARTIFACT_MISSING"


@pytest.mark.parametrize("limit", [20, 100, 1000])
def test_bounded_preview_and_original_download(manager, limit):
    path = manager.root / "fixture-deg/table.csv"
    before = sha256(path)
    result = manager.preview_table("fixture-deg", "deg_csv", limit=limit)
    assert len(result.frame) == limit and result.truncated and result.total_rows is None
    assert result.bytes_read < path.stat().st_size
    assert manager.download("fixture-deg", "deg_csv")[1] == path.read_bytes()
    assert sha256(path) == before


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5])
def test_invalid_limit(manager, limit):
    with pytest.raises(ProjectError):
        manager.preview_table("fixture-deg", "deg_csv", limit=limit)


def test_large_table_reads_only_bounded_prefix(tmp_path):
    """Explicit synthetic scale fixture, not a biological analysis result."""
    path = tmp_path / "large.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("gene_id,log2FoldChange,padj\n")
        for index in range(100_000):
            handle.write(f"fixture_{index},2,0.01\n")
    for limit in (100, 1000):
        preview = preview_table(path, limit=limit)
        assert len(preview.frame) == limit
        assert preview.truncated and preview.total_rows is None
        assert preview.bytes_read < path.stat().st_size / 50
        assert preview.frame.iloc[-1]["gene_id"] == f"fixture_{limit - 1}"


def test_tsv_quoted_multiline_and_header_only(tmp_path):
    path = tmp_path / "test.tsv"
    path.write_text('gene_id\tnote\n001\t"a\nb"\n', encoding="utf-8", newline="")
    preview = preview_table(path)
    assert preview.frame.iloc[0].tolist() == ["001", "a\nb"] and preview.total_rows == 1
    path.write_text("gene_id\tnote\n", encoding="utf-8")
    assert preview_table(path).total_rows == 0


def test_oversize_and_malformed_preview(tmp_path, monkeypatch):
    path = tmp_path / "data.csv"
    path.write_text("a,b\n" + "x" * 120 + ",y\n", encoding="utf-8")
    monkeypatch.setattr("bulk_rnaseq.workspace.result_preview.MAX_TABLE_BYTES", 100)
    with pytest.raises(ProjectError) as error:
        preview_table(path)
    assert error.value.code == "TABLE_PREVIEW_TOO_LARGE"
    path.write_text("a,b\n1\n", encoding="utf-8")
    with pytest.raises(ProjectError):
        preview_table(path)


def test_image_json_and_corrupt_records(manager):
    assert manager.preview_image("fixture-deg", "plot").startswith(b"\x89PNG")
    compact = manager.preview_json("fixture-deg", "summary_json")
    assert "local path hidden" in str(compact)
    (manager.root / "fixture-deg/figure.png").write_bytes(b"not png")
    with pytest.raises(ProjectError):
        manager.preview_image("fixture-deg", "plot")
    broken = manager.root / "broken"
    broken.mkdir()
    (broken / "summary.json").write_text("{bad")
    manager.refresh()
    assert len(manager.runs) == 3 and manager.diagnostics


def test_legacy_and_duplicate_id(manager):
    directory = manager.root / "legacy"
    directory.mkdir()
    (directory / "table.tsv").write_text("a\tb\n1\t2\n")
    write_json(directory / "summary.json", {"schema_version": "1.0", "operation": "go_enrichment",
        "status": "succeeded", "run_id": "legacy-run", "output_dir": "C:/old/location",
        "artifacts": {"enrichment": "table.tsv"}, "metrics": {"n_enriched": 1}})
    assert manager.get_run("legacy-run").summary["n_enriched"] == 1
    assert manager.preview_table("legacy-run", "enrichment").total_rows == 1
    record(manager.root / "duplicate", run_id="fixture-deg")
    manager.refresh()
    with pytest.raises(ProjectError) as error:
        manager.get_run("fixture-deg")
    assert error.value.code == "DUPLICATE_RUN_ID"


def test_tool_report_dedup_chat_and_summary_failure(manager):
    request = manager.root / "request-id"
    result = record(request, run_id="skill", skill="rnaseq.pca")
    write_json(request / "tool_execution.json", {"request_id": "request-id", "status": "succeeded", "skill_result": result.to_dict(), "stage": "skill_execution"})
    store = ChatStore(manager.manager, "First")
    store.append([{"role": "assistant", "content": "Fixture", "timestamp": now(), "skill_name": "rnaseq.pca", "run_id": "skill", "status": "failed",
        "request_id": "chat-id", "result": {"backend_request_id": "request-id", "user_message": "fixture PCA request", "final_response_status": "failed"}}])
    run = manager.get_run("request-id")
    assert run.run_id == "skill" and run.status == "succeeded"
    assert run.triggered_by == "fixture PCA request" and run.final_response_status == "failed"
    assert sum(r.run_id == "skill" for r in manager.list_runs()) == 1


def test_failed_request_without_skill_result_and_scan_bound(manager):
    path = manager.root / "request"
    path.mkdir()
    write_json(path / "tool_execution.json", {"request_id": "request", "skill_name": "rnaseq.deg", "status": "failed",
        "stage": "argument_validation", "error_code": "INVALID_TOOL_ARGUMENTS", "message": "Fixture error"})
    assert manager.get_run("request").stage == "argument_validation"
    bounded = ResultManager(manager.manager, "First", max_entries=1)
    bounded.list_runs()
    assert bounded.diagnostics


def test_json_compact_view():
    value = display_value({"items": list(range(100)), "processes": ["private"], "output": "C:/private/file.csv"})
    assert len(value["items"]) == 31 and "processes" not in value
    assert value["output"] == "[local path hidden]"


def test_link_replacement_rechecked_at_download(manager, monkeypatch):
    manager.list_runs()
    target = manager.root / "fixture-deg"
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda p: p == target or original(p))
    with pytest.raises(ProjectError):
        manager.download("fixture-deg", "deg_csv")


def test_artifact_must_be_declared(manager):
    (manager.root / "fixture-deg/undeclared.txt").write_text("private")
    with pytest.raises(ProjectError):
        manager.download("fixture-deg", "undeclared.txt")


def test_apptest_filters_preview_empty_failed_switch_and_refresh(manager, monkeypatch, apptest_ipc):
    monkeypatch.setenv("RNASEQ_WORKSPACE_ROOT", str(manager.manager.root))
    at = AppTest.from_file(str(APP)).run()
    service = FileImportService(manager.manager)
    set_current_project(at.session_state[STATE_KEY], service, "First")
    at.session_state[STATE_KEY].selected_tab = "Results"
    at.run()
    assert not at.exception and element(at.selectbox, "Analysis run")
    element(at.selectbox, "Skill").select("rnaseq.deg").run()
    assert any(metric.label == "Up" and metric.value == "600" for metric in at.metric)
    element(at.radio, "Artifact category").set_value("Tables").run()
    assert any(len(frame.value) == 100 for frame in at.dataframe)
    assert at.download_button
    element(at.selectbox, "Skill").select("rnaseq.kegg").run()
    assert any("Analysis completed successfully" in item.value for item in at.info)
    element(at.selectbox, "Skill").select("All").run()
    element(at.selectbox, "Status").select("Failed").run()
    assert any("FIXTURE_FAILURE" in item.value for item in at.error)
    element(at.button, "Refresh Results").click().run()
    assert not at.exception
    element(at.selectbox, "Projects").select("Second").run()
    state = at.session_state[STATE_KEY]
    assert state.selected_run_id is None
    at.session_state[widget_key(state, "tabs")] = "Results"
    at.run()
    assert any("No analysis results" in item.value for item in at.info)
