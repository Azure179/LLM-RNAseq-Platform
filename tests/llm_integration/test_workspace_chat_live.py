"""Opt-in Streamlit widget E2E. No mocked decisions, summaries or statistics.

AppTest exercises real UI code but is not a graphical browser/manual acceptance.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from app.state import STATE_KEY, set_current_project
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import FileImportService, ProjectManager
from bulk_rnaseq.workspace.chat_controller import ChatController


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.llm


def open_chat(request, monkeypatch, key="Real_DATA_UI_TEST"):
    manager = ProjectManager(ROOT / "workspace")
    service = FileImportService(manager)
    manager.open_project(key)
    monkeypatch.setenv("RNASEQ_WORKSPACE_ROOT", str(manager.root))
    def controller_factory(manager, unused_env):
        return ChatController(manager, Path(request.config.getoption("--llm-env-file")), enable_routing=False)
    monkeypatch.setattr("app.views.assistant.ChatController", controller_factory)
    at = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=300).run()
    set_current_project(at.session_state[STATE_KEY], service, key)
    at.run()
    return at, service


def submit_and_record(at, service, request, user):
    state = at.session_state[STATE_KEY]
    key = state.current_project.key
    old = len(state.chat_messages)
    at.chat_input[0].set_value(user).run(timeout=300)
    assert not at.exception
    state = at.session_state[STATE_KEY]
    assert len(state.chat_messages) == old + 2, state.notice
    result = state.chat_messages[-1]["result"]
    directory = Path(request.config.getoption("--llm-report-dir")) / "phase_6_5_4" / result["request_id"]
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "ui_chat_test.json", {
        "test_case": request.node.name, "surface": "Streamlit AppTest (not graphical browser)",
        "project": key, "user_input": user, "chat_result": result})
    assert state.chat_messages[-1]["content"] in [text.value for text in at.text]
    # A normal UI rerender restores history without an additional request/run.
    requests_before = len(list((service.manager.root / key / "runtime/chat_requests").glob("*.json")))
    at.run(timeout=300)
    assert len(list((service.manager.root / key / "runtime/chat_requests").glob("*.json"))) == requests_before
    return result


@pytest.mark.integration
@pytest.mark.parametrize("skill,user", [
    ("rnaseq.pca", "帮我画一下样本PCA。"),
    ("rnaseq.deg", "以T为对照，比较OT的差异表达。"),
])
def test_real_chat_analysis(skill, user, request, monkeypatch):
    at, service = open_chat(request, monkeypatch)
    project = service.manager.open_project("Real_DATA_UI_TEST")
    inputs = [project.root / getattr(project.metadata.files, role) for role in ("count_matrix", "metadata")]
    before = [sha256(path) for path in inputs]
    result = submit_and_record(at, service, request, user)
    assert result["status"] == "succeeded", result
    assert result["selected_skill"] == skill and result["request_count"] == 2
    assert result["final_response_status"] == "succeeded"
    assert result["artifacts"] and all((project.root / artifact["path"]).is_file() for artifact in result["artifacts"])
    report = json.loads((project.root / "results" / result["backend_request_id"] / "tool_execution.json").read_text(encoding="utf-8"))
    assert report["skill_result"]["metadata"]["processes"][0]["return_code"] == 0
    assert len(report["llm_response"]["message"]["tool_calls"]) == 1
    if skill == "rnaseq.deg":
        assert report["arguments"]["treatment"] == "OT" and report["arguments"]["control"] == "T"
        assert "上调基因数为" + str(result["summary"]["upregulated_genes"]) in result["assistant_message"]
    else:
        assert f"PC1解释方差为{result['summary']['pc1_variance']:.2f}%" in result["assistant_message"]
    assert [sha256(path) for path in inputs] == before
    assert at.session_state[STATE_KEY].results


@pytest.mark.parametrize("user,expected", [
    ("帮我做差异表达。", "needs_input"),
    ("做单细胞Seurat聚类。", "unsupported"),
    ("以T为对照比较OT，然后画火山图，再做KEGG。", "unsupported"),
])
def test_real_chat_no_execution(user, expected, request, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No Skill may execute for missing, unsupported or multistep requests")
    monkeypatch.setattr(SkillRegistry, "execute", forbidden)
    at, service = open_chat(request, monkeypatch)
    result = submit_and_record(at, service, request, user)
    assert result["status"] == expected, result
    assert not result["artifacts"] and result["request_count"] == 1


def test_real_chat_missing_go(request, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Missing dependencies must stop before execution")
    monkeypatch.setattr(SkillRegistry, "execute", forbidden)
    manager = ProjectManager(ROOT / "workspace")
    key = "Chat_Missing_GO_" + uuid4().hex[:8]
    manager.create_project(key, description="Phase 6.5.4 missing dependency test; no GO mapping")
    source = manager.open_project("Real_DATA_UI_TEST")
    service = FileImportService(manager)
    for role in ("count_matrix", "metadata"):
        result = service.import_file(key, source.root / getattr(source.metadata.files, role), role=role)
        assert result.status == "succeeded", result
    at, service = open_chat(request, monkeypatch, key)
    result = submit_and_record(at, service, request, "做GO富集。")
    assert result["status"] == "needs_input" and result["selected_skill"] == "rnaseq.go", result
    assert "gene_to_go_mapping" in result["missing"] and "gene_list" in result["missing"]
    assert result["request_count"] == 1 and not result["artifacts"]
