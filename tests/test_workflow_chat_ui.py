"""Offline product integration: real adapters/executor, explicitly synthetic API/Skills."""

from io import BytesIO
import csv
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import Mock
import httpx
import pytest
from streamlit.testing.v1 import AppTest
from app.state import STATE_KEY, set_current_project
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.single_request import SingleRequestRunner
from bulk_rnaseq.llm.result_formatter import METRICS
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import ProjectManager, FileImportService
from bulk_rnaseq.workspace.chat_controller import ChatController
from bulk_rnaseq.workspace.chat_store import ChatStore
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.ui_adapter import import_upload
from test_workspace_ui import APP, DATA, apptest_ipc
from plot_helpers import fixture_png


def steps(roots=False):
    deg = {"step_id": "deg", "skill_name": "rnaseq.deg", "parameters": {"treatment": "OT", "control": "T"}}
    if roots:
        return [{"step_id": "pca", "skill_name": "rnaseq.pca"}, deg]
    binding = {"source": "workflow_step", "step_id": "deg", "output": "deg_csv"}
    return [deg, {"step_id": "volcano", "skill_name": "rnaseq.volcano", "depends_on": ["deg"],
                  "input_bindings": {"deg_result_file": binding}},
            {"step_id": "kegg", "skill_name": "rnaseq.kegg", "depends_on": ["deg"], "input_bindings": {
                "gene_list": {**binding, "output": "filtered_csv", "gene_subset": "up"}, "analysis_genes": binding}}]


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setattr("subprocess.run", Mock(side_effect=AssertionError("No R in offline tests")))
    manager = ProjectManager(tmp_path / "workspace")
    manager.create_project("First")
    manager.create_project("Second")
    service = FileImportService(manager)
    for role, (name, data) in DATA.items():
        assert import_upload(service, "First", name, BytesIO(data), role).status == "succeeded"
    registry = create_default_registry()
    answers, requests, calls, fail = [], [], [], set()
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(manager.root) not in json.dumps(payload)
        prompt = payload["messages"][0]["content"]
        if "VERIFIED_WORKFLOW=" in prompt:
            facts = json.loads(prompt.split("VERIFIED_WORKFLOW=", 1)[1])["facts"]
            answer = {"final_response": "。".join(facts) + "。"}
        else:
            answer = answers.pop(0)
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": "fixture service failure"})
        return httpx.Response(200, json={"id": "fixture", "model": "mock-only", "choices": [{"finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    client = DeepSeekClient(DeepSeekConfig("fixture-secret"), transport=httpx.MockTransport(respond))
    def execute(name, inputs):
        calls.append(name)
        if name in fail:
            return SkillResult("failed", name, uuid4().hex, error_code="FIXTURE_FAILURE", message="Synthetic failure")
        output = Path(inputs["output_dir"])
        output.mkdir(parents=True)
        table = output / "fixture.csv"
        table.write_text("gene_id,log2FoldChange,pvalue,padj\ng1,2,0.001,0.01\ng2,-2,0.001,0.01\n")
        plot = output / "fixture.png"
        plot.write_bytes(fixture_png())
        outputs = {"plot": str(plot)}
        if name == "rnaseq.deg":
            outputs.update(deg_csv=str(table), filtered_csv=str(table))
        if name == "rnaseq.volcano":
            outputs["plot_data"] = str(table)
        metrics = {k: 2 for k in METRICS[name]}
        if name == "rnaseq.deg":
            metrics.update(genes_analyzed=2, genes_with_pvalue=2, significant_genes=2, upregulated_genes=1, downregulated_genes=1)
        result = SkillResult("succeeded", name, uuid4().hex, inputs={"parameters": dict(inputs)}, summary=metrics,
            outputs=outputs, metadata={"provenance": {"artifact_sha256": {Path(v).name: sha256(Path(v)) for v in outputs.values()}}})
        write_json(output / "summary.json", result.to_dict())
        return result
    monkeypatch.setattr(registry, "execute", execute)
    controller = ChatController(manager, tmp_path / "unused.env", runner_factory=lambda: SingleRequestRunner(client, registry))
    return controller, answers, requests, calls, fail


def run_workflow(host, roots=False):
    controller, answers, _, _, _ = host
    answers.extend([{"request_type": "workflow"}, {"status": "ready", "steps": steps(roots)}])
    return controller.submit("First", "以T为对照比较OT，画PCA" if roots else "以T为对照比较OT，画火山图，对上调做KEGG", str(uuid4()))


@pytest.mark.parametrize("roots,failure,expected", [(False, None, "succeeded"), (True, None, "succeeded"),
    (False, "rnaseq.volcano", "partial"), (False, "rnaseq.deg", "failed")])
def test_workflow_chain_persistence_and_branch_status(host, roots, failure, expected):
    controller, _, requests, calls, failures = host
    if failure:
        failures.add(failure)
    result = run_workflow(host, roots)
    assert result.status == expected, result
    assert result.workflow_id and result.steps
    assert result.final_response_status == ("failed" if expected == "failed" else "succeeded"), result
    before = len(calls), len(requests)
    assert controller.submit("First", result.user_message, result.request_id) == result
    assert before == (len(calls), len(requests))
    if failure == "rnaseq.volcano":
        assert next(s for s in result.steps if s["skill_name"] == "rnaseq.kegg")["status"] == "succeeded"
        assert "部分成功" in result.assistant_message
    if failure == "rnaseq.deg":
        assert len(calls) == 1 and len(requests) == 2
        assert [s["status"] for s in result.steps].count("skipped") == 2
    browser = ResultManager(controller.manager, "First")
    collection = browser.list_collections()[0]
    assert collection["id"] == result.workflow_id and collection["status"] == expected
    assert not ResultManager(controller.manager, "Second").list_collections()
    assert len(controller.history("First")) == 2 and not controller.history("Second")


@pytest.mark.parametrize("decision,status", [({"status": "needs_input", "missing_parameters": ["treatment", "control"], "message": "Missing comparison"}, "needs_input"),
    ({"status": "unsupported", "message": "Unsupported"}, "unsupported")])
def test_planner_nonready_never_executes(host, decision, status):
    controller, answers, _, calls, _ = host
    answers.extend([{"request_type": "workflow"}, decision])
    result = controller.submit("First", "做差异表达、火山图和KEGG", str(uuid4()))
    assert result.status == status and not calls
    if status == "needs_input":
        assert all(v in result.assistant_message for v in ("treatment", "control", "OT", "T"))


def test_customization_recent_uses_exact_chat_run(host):
    controller, answers, _, calls, _ = host
    source = run_workflow(host)
    run_workflow(host)  # More than one compatible source, explicit recent chooses latest chat response.
    latest = controller.history("First")[-1]["result"]
    expected = next(s["run_id"] for s in latest["steps"] if s["skill_name"] == "rnaseq.volcano")
    answers.extend([{"request_type": "customization", "target_skill": "rnaseq.volcano", "source_reference": "recent"},
        {"mode": "parameter_override", "target_skill": "rnaseq.volcano", "parameters": {"top_n_labels": 20, "color_up": "red", "color_down": "blue"}}])
    before = len(calls)
    result = controller.submit("First", "把刚才火山图改成红蓝配色，标Top20", str(uuid4()))
    assert result.status == "succeeded", result
    assert result.source_run_id == expected and result.customization_id
    assert calls[before:] == ["rnaseq.volcano"]
    browser = ResultManager(controller.manager, "First")
    assert browser.get_run(expected).status == "succeeded"
    assert browser.get_run(expected).comparison == "OT vs T"
    assert browser.get_run(result.run_id).source_run_id == expected
    assert len(browser.list_collections("Customization")) == 1


def test_ambiguous_source_resume_once_and_foreign_rejected(host):
    controller, answers, requests, calls, _ = host
    run_workflow(host)
    run_workflow(host)
    answers.append({"request_type": "customization", "target_skill": "rnaseq.volcano"})
    result = controller.submit("First", "火山图改红色", str(uuid4()))
    assert result.status == "needs_input" and len(result.source_options) == 2
    before = len(requests), len(calls)
    assert controller.submit("First", result.user_message, result.request_id) == result
    rejected = controller.submit("First", result.user_message, result.request_id, source_run_id="foreign-run")
    assert rejected.error_code == "INVALID_SOURCE_SELECTION" and (len(requests), len(calls)) == before
    answers.append({"mode": "parameter_override", "target_skill": "rnaseq.volcano", "parameters": {"color_up": "red"}})
    resumed = controller.submit("First", result.user_message, result.request_id, source_run_id=result.source_options[0]["run_id"])
    assert resumed.status == "succeeded", resumed
    assert len(requests) == before[0] + 1 and len(calls) == before[1] + 1  # No repeat router/Workflow.
    assert controller.submit("First", result.user_message, result.request_id) == resumed
    assert len([r for r in controller.history("First") if r["request_id"] == result.request_id]) == 2


def test_annotation_from_imported_eggnog_not_inferred(host, tmp_path):
    controller, answers, _, calls, _ = host
    run_workflow(host)
    annotation = tmp_path / "egg.tsv"
    annotation.write_text("#query\tDescription\tGOs\tKEGG_Pathway\ng1\tsex differentiation\tGO:0000001\tmap00010\ng2\tlipid metabolism\tGO:0000002\tmap00020\n")
    assert controller.service.import_eggnog("First", annotation, replace=True).status == "succeeded"
    answers.extend([{"request_type": "customization", "target_skill": "rnaseq.deg"}, {"mode": "annotation_filter",
        "target_skill": "rnaseq.deg", "parameters": {"gene_subset": "up", "keywords": ["sex differentiation"], "annotation_sources": ["gene_description"]}}])
    before = len(calls)
    result = controller.submit("First", "筛选性别分化相关的上调DEG", str(uuid4()))
    assert result.status == "succeeded" and result.summary["n_matched_genes"] == 1, result
    assert result.summary["n_evidence_rows"] == 1  # Not doubled by the two imported eggNOG originals.
    assert len(calls) == before
    browser = ResultManager(controller.manager, "First")
    data = browser.download(result.run_id, "filtered_table")[1].decode()
    assert [r["gene_id"] for r in csv.DictReader(data.splitlines())] == ["g1"]


def test_unsupported_customization_safe(host):
    controller, answers, _, calls, _ = host
    run_workflow(host)
    answers.extend([{"request_type": "customization", "target_skill": "rnaseq.volcano"},
        {"mode": "unsupported", "target_skill": "rnaseq.volcano", "reason": "Requires unsafe code"}])
    before = len(calls)
    result = controller.submit("First", "任意动态脚本改变图形", str(uuid4()))
    assert result.status == "unsupported" and result.customization_id and len(calls) == before


@pytest.mark.parametrize("answer", [429, {"request_type": "workflow", "shell": "bad"}, {"request_type": "alien"}])
def test_route_failure_persisted_not_repeated(host, answer):
    controller, answers, requests, calls, _ = host
    answers.append(answer)
    result = controller.submit("First", "测试错误", str(uuid4()))
    assert result.status == "failed" and not calls
    assert controller.submit("First", result.user_message, result.request_id) == result
    assert len(requests) == 1 and len(controller.history("First")) == 2


def test_apptest_workflow_restart_and_results(host, monkeypatch, apptest_ipc):
    controller, answers, requests, calls, _ = host
    answers.extend([{"request_type": "workflow"}, {"status": "ready", "steps": steps()}])
    monkeypatch.setenv("RNASEQ_WORKSPACE_ROOT", str(controller.manager.root))
    monkeypatch.setattr("app.views.assistant.ChatController", lambda *args: controller)
    at = AppTest.from_file(str(APP)).run()
    set_current_project(at.session_state[STATE_KEY], controller.service, "First")
    at.run().chat_input[0].set_value("以T为对照比较OT，画火山图，上调KEGG").run(timeout=30)
    assert not at.exception and len(calls) == 3
    at.run()
    reopened = AppTest.from_file(str(APP)).run()
    set_current_project(reopened.session_state[STATE_KEY], controller.service, "First")
    reopened.run()
    assert len(reopened.chat_message) == 2 and len(calls) == 3 and len(requests) == 3
    next(b for b in reopened.button if b.label == "View Results").click().run()
    assert not reopened.exception
    assert reopened.session_state[STATE_KEY].selected_collection_id
    assert any(s.label == "Workflow / Customization" for s in reopened.selectbox)


def test_apptest_customization_source_choice(host, monkeypatch, apptest_ipc):
    controller, answers, _, calls, _ = host
    run_workflow(host)
    run_workflow(host)
    registry = controller.runner_factory().registry
    original_execute = registry.execute
    def with_missing_label_warning(name, inputs):
        result = original_execute(name, inputs)
        result.warnings.append({"code": "LABEL_GENES_UNAVAILABLE", "message": "Fixture exact IDs not found.", "details": {"genes": ["missing_gene"]}})
        return result
    monkeypatch.setattr(registry, "execute", with_missing_label_warning)
    answers.extend([{"request_type": "customization", "target_skill": "rnaseq.volcano"},
        {"mode": "parameter_override", "target_skill": "rnaseq.volcano", "parameters": {"top_n_labels": 20}}])
    monkeypatch.setenv("RNASEQ_WORKSPACE_ROOT", str(controller.manager.root))
    monkeypatch.setattr("app.views.assistant.ChatController", lambda *args: controller)
    at = AppTest.from_file(str(APP)).run()
    set_current_project(at.session_state[STATE_KEY], controller.service, "First")
    at.run().chat_input[0].set_value("火山图标Top20").run(timeout=30)
    assert not at.exception
    selector = next(s for s in at.selectbox if s.label == "Source result")
    selector.select_index(0).run()
    next(b for b in at.button if b.label == "Continue with selected result").click().run(timeout=30)
    assert not at.exception
    assert len(calls) == 7
    at.run()
    assert len(calls) == 7 and len(at.get("image")) >= 1, at
    assert any("LABEL_GENES_UNAVAILABLE" in warning.value for warning in at.warning)


def test_changed_import_rejected_before_workflow_execute(host, monkeypatch):
    from bulk_rnaseq.workflow.planner import WorkflowPlanner
    controller, answers, _, calls, _ = host
    original = WorkflowPlanner.plan
    def change_after_planning(self, *args, **kwargs):
        response = original(self, *args, **kwargs)
        path = controller.manager.get_project_context("First").count_matrix
        path.write_text(path.read_text().replace("g1,1,2", "g1,9,2"))
        return response
    monkeypatch.setattr(WorkflowPlanner, "plan", change_after_planning)
    result = run_workflow(host)
    assert result.status == "failed" and result.error_code == "IMPORTED_FILE_CHANGED", result
    assert not calls


def test_progress_observer_cannot_abort_workflow(host):
    controller, answers, _, calls, _ = host
    answers.extend([{"request_type": "workflow"}, {"status": "ready", "steps": steps()}])
    observed = []
    def broken_ui(stage, name):
        observed.append(stage)
        if stage == "step_running":
            raise RuntimeError("Synthetic UI disconnect")
    result = controller.submit("First", "以T为对照比较OT，火山图，上调KEGG", str(uuid4()), progress=broken_ui)
    assert result.status == "succeeded" and len(calls) == 3
    assert observed.count("step_running") == 3 and observed.count("step_succeeded") == 3


def test_missing_workflow_input_blocks_all_steps(host):
    controller, answers, _, calls, _ = host
    controller.service.remove_file("First", "metadata")
    result = run_workflow(host)
    assert result.status == "needs_input" and not calls, result


def test_foreign_project_resume_and_unsupported_pca_customization(host):
    controller, answers, _, calls, _ = host
    answers.append({"request_type": "customization", "target_skill": "rnaseq.pca"})
    result = controller.submit("First", "修改PCA为复杂自定义图", str(uuid4()))
    assert result.status == "unsupported" and not calls
    denied = controller.submit("Second", result.user_message, result.request_id, source_run_id="first-project-run")
    assert denied.error_code == "INVALID_SOURCE_SELECTION" and not controller.history("Second")
