"""Free chat adapter fixtures; simulated backend outputs are NOT real analysis evidence."""

from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import httpx
from streamlit.testing.v1 import AppTest

from app.state import STATE_KEY, WorkspaceState, set_current_project
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.single_request import SingleRequestResult
from bulk_rnaseq.llm.single_request import SingleRequestRunner
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.tool_runner import ToolExecutionResult
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workspace import FileImportService, ProjectError, ProjectManager
from bulk_rnaseq.workspace.chat_controller import ChatController
from bulk_rnaseq.workspace.chat_store import ChatStore
from bulk_rnaseq.workspace.ui_adapter import import_upload
from test_workspace_ui import APP, DATA, apptest_ipc


@pytest.fixture
def chat(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=AssertionError("No R in free chat tests")))
    manager = ProjectManager(tmp_path / "workspace")
    for key in ("First", "Second"):
        manager.create_project(key)
    service = FileImportService(manager)
    for role in ("count_matrix", "metadata"):
        filename, content = DATA[role]
        assert import_upload(service, "First", filename, BytesIO(content), role).status == "succeeded"
    client = SimpleNamespace(sanitize_for_audit=lambda value: value.replace("fixture-secret", "[redacted]") if isinstance(value, str) else value)
    runner = SimpleNamespace(client=client, registry=create_default_registry(), run=Mock())
    factory = Mock(return_value=runner)
    controller = ChatController(manager, tmp_path / ".env", runner_factory=factory, enable_routing=False)
    return controller, runner, factory


def decision(status="needs_input", *, skill="rnaseq.deg", missing=None):
    return SingleRequestResult(status=status, usage={}, execution=ToolExecutionResult(
        status=status, stage="tool_selection", request_id=str(uuid4()), skill_name=skill,
        missing_parameters=missing or [], message="Fixture decision"))


def mock_success(runner, skill="rnaseq.pca", *, summary_failure=False):
    def run(text, context, *, progress, before_execute):
        backend_id = str(uuid4())
        directory = context.output_root / backend_id
        directory.mkdir()
        params = {"treatment": "OT", "control": "T"} if skill == "rnaseq.deg" else {"group_column": "condition"}
        validated = context.inject(skill, params, directory)
        progress("understanding", None)
        progress("selected", skill)
        progress("validating", skill)
        before_execute(skill, validated)
        progress("running", skill)
        summary = directory / "summary.json"
        write_json(summary, {"fixture": True})
        outputs = {"summary_json": str(summary)}
        if skill == "rnaseq.deg":
            for label in ("deg_csv", "filtered_csv"):
                path = directory / (label + ".csv")
                path.write_text("gene_id,log2FoldChange,padj\ng1,2,0.01\n", encoding="utf-8")
                outputs[label] = str(path)
        source = {"status": "succeeded", "run_id": "fixture-run", "outputs": outputs, "summary": {"fixture": True}}
        progress("summarizing", skill)
        return SingleRequestResult(status="failed" if summary_failure else "succeeded", usage={"total_tokens": 30},
            request_count=2, final_response="Fixture summary only", error_code="LLM_RESPONSE_VALIDATION_FAILED" if summary_failure else None,
            execution=ToolExecutionResult(status="succeeded", stage="skill_execution", request_id=backend_id,
                                         skill_name=skill, skill_result=source))
    runner.run.side_effect = run


def test_no_project_does_not_construct_client(chat):
    controller, _, factory = chat
    assert controller.submit(None, "PCA", str(uuid4())).error_code == "PROJECT_REQUIRED"
    factory.assert_not_called()


def test_empty_project_no_backend(chat):
    controller, runner, _ = chat
    result = controller.submit("Second", "PCA", str(uuid4()))
    assert result.status == "needs_input" and result.error_code == "MISSING_PROJECT_INPUTS"
    runner.run.assert_not_called()


@pytest.mark.parametrize("text", ["", "a" * 4001, "fixture-secret"])
def test_invalid_or_credential_text_not_persisted(chat, text):
    controller, runner, _ = chat
    assert controller.submit("First", text, str(uuid4())).error_code == "INVALID_CHAT_INPUT"
    assert not controller.history("First")
    runner.run.assert_not_called()


@pytest.mark.parametrize("status,missing", [("needs_input", ["treatment", "control"]), ("unsupported", []),
                                            ("needs_input", ["gene_to_go_mapping", "gene_list"])])
def test_local_decisions_history_and_isolation(chat, status, missing):
    controller, runner, _ = chat
    runner.run.return_value = decision(status, missing=missing)
    result = controller.submit("First", "fixture prompt", str(uuid4()))
    assert result.status == status
    assert all(key in result.assistant_message for key in missing)
    if missing:
        assert "OT" in result.assistant_message and "T" in result.assistant_message
    assert len(controller.history("First")) == 2 and controller.history("Second") == []
    restored = ChatController(ProjectManager(controller.manager.root), Path("unused.env"), runner_factory=Mock())
    assert restored.history("First") == controller.history("First")
    state = WorkspaceState()
    set_current_project(state, controller.service, "First")
    assert len(state.chat_messages) == 2
    set_current_project(state, controller.service, "Second")
    assert state.chat_messages == []


def test_success_context_progress_and_at_most_once(chat):
    controller, runner, factory = chat
    mock_success(runner)
    stages = []
    request_id = str(uuid4())
    result = controller.submit("First", "fixture PCA", request_id, progress=lambda stage, name: stages.append(stage))
    assert result.status == "succeeded", result
    assert stages == ["understanding", "selected", "validating", "running", "summarizing", "completed"]
    context = runner.run.call_args.args[1]
    assert context.project_root == controller.manager.root / "First"
    assert context.bindings["rnaseq.pca"]["count_matrix"].startswith("input/")
    assert all(item["path"].startswith("results/") for item in result.artifacts)
    assert controller.submit("First", "different text", request_id) == result
    assert runner.run.call_count == factory.call_count == 1


def test_submitted_or_locked_request_never_retries(chat):
    controller, runner, _ = chat
    store = ChatStore(controller.manager, "First")
    request_id = str(uuid4())
    store.save_request(request_id, {"status": "submitted"})
    assert controller.submit("First", "PCA", request_id).error_code == "REQUEST_ALREADY_SUBMITTED"
    with store.lock():
        assert controller.submit("First", "PCA", str(uuid4())).error_code == "CHAT_BUSY"
    runner.run.assert_not_called()


def test_clear_only_history(chat):
    controller, runner, _ = chat
    mock_success(runner)
    request_id = str(uuid4())
    result = controller.submit("First", "PCA", request_id)
    store = ChatStore(controller.manager, "First")
    with pytest.raises(ProjectError):
        store.clear()
    files = controller.manager.open_project("First").metadata.files
    store.clear(confirm=True)
    assert store.history() == [] and store.previous(request_id)["status"] == "completed"
    assert list(store.path("chat_archive").glob("*.json"))
    assert controller.manager.open_project("First").metadata.files == files
    assert all((controller.manager.root / "First" / item["path"]).is_file() for item in result.artifacts)
    assert controller.submit("First", "PCA", request_id) == result
    assert runner.run.call_count == 1


def test_summary_failure_keeps_outputs_and_does_not_offer_rerun(chat):
    controller, runner, _ = chat
    mock_success(runner, summary_failure=True)
    result = controller.submit("First", "PCA", str(uuid4()))
    assert result.status == "failed" and result.artifacts and "分析文件已生成" in result.assistant_message


def test_upstream_from_actual_output_record_invalidated_on_replace(chat):
    controller, runner, _ = chat
    mock_success(runner, "rnaseq.deg")
    result = controller.submit("First", "fixture DEG", str(uuid4()))
    assert result.status == "succeeded", result
    store = ChatStore(controller.manager, "First")
    context = controller._context("First", store)
    assert context.bindings["rnaseq.volcano"]["deg_result_file"].endswith("deg_csv.csv")
    assert context.bindings["rnaseq.go"]["gene_list"].endswith("filtered_csv.csv")
    assert context.bindings["rnaseq.kegg"]["analysis_genes"].endswith("deg_csv.csv")
    filename, content = DATA["count_matrix"]
    import_upload(controller.service, "First", filename, BytesIO(content), "count_matrix", replace=True)
    assert "deg_result_file" not in controller._context("First", store).bindings["rnaseq.volcano"]


def test_corrupt_history_blocks_execution(chat):
    controller, runner, _ = chat
    ChatStore(controller.manager, "First").path("chat_history.json").write_text("not JSON", encoding="utf-8")
    assert controller.submit("First", "PCA", str(uuid4())).error_code == "CHAT_RECORD_INVALID"
    runner.run.assert_not_called()


def test_apptest_chat_rerun_restore_and_results(chat, monkeypatch, apptest_ipc):
    controller, runner, _ = chat
    mock_success(runner)
    monkeypatch.setenv("RNASEQ_WORKSPACE_ROOT", str(controller.manager.root))
    monkeypatch.setattr("app.views.assistant.ChatController", lambda *args: controller)
    at = AppTest.from_file(str(APP)).run()
    set_current_project(at.session_state[STATE_KEY], controller.service, "First")
    at.run()
    at.chat_input[0].set_value("画 PCA").run(timeout=30)
    assert not at.exception
    assert len(at.chat_message) == 2
    assert runner.run.call_count == 1
    assert at.session_state[STATE_KEY].results
    at.run()
    assert runner.run.call_count == 1
    reopened = AppTest.from_file(str(APP)).run()
    set_current_project(reopened.session_state[STATE_KEY], controller.service, "First")
    reopened.run()
    assert len(reopened.chat_message) == 2 and runner.run.call_count == 1
    next(button for button in reopened.button if button.label == "View Results").click().run()
    assert reopened.session_state[STATE_KEY].selected_tab == "Results"


def test_audit_failure_does_not_change_completed_analysis(chat, monkeypatch):
    controller, runner, _ = chat
    mock_success(runner)
    monkeypatch.setattr("bulk_rnaseq.workspace.chat_controller.run_logger", Mock(side_effect=OSError("disk full")))
    result = controller.submit("First", "PCA", str(uuid4()))
    assert result.status == "succeeded" and result.artifacts
    assert result.warnings[-1]["code"] == "CHAT_AUDIT_IO_ERROR"
    assert ChatStore(controller.manager, "First").previous(result.request_id)["status"] == "completed"


def test_chat_storage_cannot_cross_project(chat):
    controller, _, _ = chat
    with pytest.raises(ProjectError):
        ChatStore(controller.manager, "First").path("../../Second/runtime/chat_history.json")


@pytest.mark.parametrize("name,args,count,code", [
    ("rnaseq_go", {}, 1, "MISSING_DEPENDENCY"),
    ("rnaseq_volcano", {}, 1, "MISSING_DEPENDENCY"),
    ("rnaseq_heatmap", {}, 1, "MISSING_DEPENDENCY"),
    ("rnaseq_kegg", {}, 1, "MISSING_DEPENDENCY"),
    ("rnaseq_deg", {"treatment": "OT", "control": "T", "alpha": 2}, 1, "INVALID_TOOL_ARGUMENTS"),
    ("rnaseq_bad", {}, 1, "LLM_TOOL_UNKNOWN_NAME"),
    ("rnaseq_pca", {}, 2, "MULTIPLE_TOOL_CALLS_NOT_SUPPORTED"),
])
def test_chat_actual_orchestrator_rejects_without_execution(chat, monkeypatch, name, args, count, code):
    controller, _, _ = chat
    registry = create_default_registry()
    execute = Mock(side_effect=AssertionError("Must not execute"))
    monkeypatch.setattr(registry, "execute", execute)
    requests = []
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(controller.manager.root) not in json.dumps(payload, ensure_ascii=False)
        call = {"id": "fixture", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        return httpx.Response(200, json={"id": "fixture", "model": "fixture", "choices": [{
            "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [call] * count}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    client = DeepSeekClient(DeepSeekConfig("sk-fixture-chat"), transport=httpx.MockTransport(respond))
    controller.runner_factory = lambda: SingleRequestRunner(client, registry)
    result = controller.submit("First", "fixture request OT vs T", str(uuid4()))
    assert result.error_code == code, result
    assert len(requests) == 1
    execute.assert_not_called()
