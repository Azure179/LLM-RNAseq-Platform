"""Offline HTTP tests: real routing parsers, services, validation, lineage and grounding.

Only the provider replies and statistical execution are synthetic fixtures.
"""

import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from bulk_rnaseq.llm.client import ChatResponse, ToolSchemaResponse
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.result_formatter import METRICS
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.workspace import FileImportService
from bulk_rnaseq.workspace.chat_store import ChatStore
from test_api_execution import api
from test_api_workflow import deg, volcano, kegg


@pytest.fixture
def assistant(api, monkeypatch):
    service = api[0].app.state.assistant_service
    provider = Mock()
    provider.sanitize_for_audit.side_effect = lambda value: value
    monkeypatch.setattr(service, "create_client", lambda: provider)
    original = service.single.registry.execute

    def execution(name, inputs):
        result = original(name, inputs)
        result.summary.clear()
        result.summary.update({key: 2 for key in METRICS[name]})
        if name == "rnaseq.deg":
            result.summary.update(significant_genes=2, upregulated_genes=1, downregulated_genes=1)
        if name == "rnaseq.kegg":
            result.summary["n_enriched"] = 0
        write_json(Path(inputs["output_dir"]) / "summary.json", result.to_dict())
        return result
    monkeypatch.setattr(service.single.registry, "execute", execution)
    return api, provider


def replies(provider, *decisions, hallucinate=False):
    pending = iter(decisions)
    def chat(text, **options):
        prompt = options.get("system_prompt", "")
        marker = next((v for v in ("\nVERIFIED_WORKFLOW=", "\nVERIFIED_RESULT=") if v in prompt), None)
        if marker:
            facts = json.loads(prompt.split(marker, 1)[1])["facts"]
            payload = {"final_response": "。".join(facts) + ("。新增99999个基因" if hallucinate else "。")}
        else:
            payload = next(pending)
        return ChatResponse("fixture", "mock-only", json.dumps(payload, ensure_ascii=False), "stop",
                            {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}, .01)
    provider.chat.side_effect = chat


def post(assistant, text="以control为对照比较treatment，画火山图并对上调基因做KEGG。", project="demo", **extra):
    return assistant[0][0].post("/assistant/analyze", json={"project_id": project, "request": text, **extra})


def workflow_replies(provider, *, hallucinate=False):
    replies(provider, {"request_type": "workflow"}, {"status": "ready", "steps": [deg(), volcano(), kegg()]}, hallucinate=hallucinate)


def test_single_uses_phase82_and_grounded_summary(assistant, monkeypatch):
    api, provider = assistant
    replies(provider, {"request_type": "single_skill"})
    provider.request_tool_call.return_value = ToolSchemaResponse("fixture", "mock-only", {"tool_calls": [{
        "id": "call", "type": "function", "function": {"name": "rnaseq_pca", "arguments": "{}"}}]},
        "tool_calls", {"total_tokens": 15}, .01)
    service = api[0].app.state.execution_service
    spy = Mock(wraps=service.execute)
    monkeypatch.setattr(service, "execute", spy)
    response = post(assistant, "做PCA")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success", body
    assert body["request_type"] == "single_skill" and body["run_id"]
    assert "PC1解释方差为2.00%" in body["response"]
    assert [name for name, _ in api[2]] == ["rnaseq.pca"]
    assert spy.call_count == 1 and body["request_count"] == 3


def test_workflow_services_lineage_and_replay(assistant, monkeypatch):
    api, provider = assistant
    workflow_replies(provider)
    service = api[0].app.state.workflow_service
    spy = Mock(wraps=service.run)
    monkeypatch.setattr(service, "run", spy)
    identity = str(uuid4())
    response = post(assistant, request_id=identity)
    body = response.json()
    assert body["status"] == "success", body
    assert body["request_type"] == "workflow" and body["workflow_id"]
    assert len(api[2]) == 3 and spy.call_count == 1
    assert "上游已筛选DEG中的上调基因" in body["response"]
    assert "未检测到显著富集结果" in body["response"]
    assert body["usage"]["total_tokens"] == 90 and body["request_count"] == 3
    assert post(assistant, request_id=identity).json() == body
    assert len(api[2]) == 3 and provider.chat.call_count == 3
    conflict = post(assistant, "改成PCA", request_id=identity)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "REQUEST_ID_CONFLICT"
    assert "private-secret" not in response.text and str(api[1].root) not in response.text
    assert all((api[1].open_project("demo").root / v["path"]).is_file() for v in body["artifacts"])


@pytest.mark.parametrize("status", ["needs_input", "unsupported"])
def test_nonexecuting_decisions(assistant, status):
    api, provider = assistant
    if status == "needs_input":
        replies(provider, {"request_type": "workflow"}, {"status": status, "steps": [],
            "missing_parameters": ["treatment", "control"], "message": "Clarify comparison"})
    else:
        replies(provider, {"request_type": status})
    body = post(assistant, "做差异表达和火山图" if status == "needs_input" else "单细胞轨迹分析").json()
    assert body["status"] == status and not api[2]
    if status == "needs_input":
        assert body["required_fields"] == ["treatment", "control"]


def test_reuse_does_not_repeat_deg_and_is_project_local(assistant):
    api, provider = assistant
    workflow_replies(provider)
    previous = post(assistant).json()
    assert previous["status"] == "success", previous
    source = next(step["run_id"] for step in previous["steps"] if step["skill_name"] == "rnaseq.deg")
    replies(provider, {"request_type": "result_reuse", "target_skill": "rnaseq.kegg", "source_reference": "recent", "gene_subset": "up"})
    body = post(assistant, "对刚才的上调DEG做KEGG").json()
    assert body["status"] == "success" and body["source_run_id"] == source, body
    assert [n for n, _ in api[2]].count("rnaseq.deg") == 1
    # Same data, different project, no historical source. No cross-project lookup.
    manager = api[1]
    manager.create_project("other")
    importer = FileImportService(manager)
    for role in ("count_matrix", "metadata"):
        context = importer.build_execution_context("demo")
        path = context.local_path(context.bindings["rnaseq.deg"][role + "_path"])
        assert importer.import_file("other", Path(path), role).status == "succeeded"
    replies(provider, {"request_type": "result_reuse", "target_skill": "rnaseq.volcano", "source_run_id": source})
    isolated = post(assistant, "用 " + source + " 画火山图", project="other").json()
    assert isolated["status"] == "needs_input", isolated
    assert isolated["error_code"] == "MISSING_UPSTREAM_DEPENDENCY"
    assert len(api[2]) == 4


def test_ambiguous_source_selection_resume(assistant):
    api, provider = assistant
    service = api[0].app.state.execution_service
    for _ in range(2):
        service.execute("rnaseq.deg", "demo", {"treatment": "treatment", "control": "control"})
    replies(provider, {"request_type": "result_reuse", "target_skill": "rnaseq.volcano"})
    body = post(assistant, "根据已有DEG画火山图").json()
    assert body["status"] == "needs_input" and body["error_code"] == "AMBIGUOUS_UPSTREAM_RESULT", body
    assert len(body["source_options"]) == 2 and len(api[2]) == 2
    resumed = post(assistant, "根据已有DEG画火山图", request_id=body["request_id"], source_run_id=body["source_options"][0]["run_id"]).json()
    assert resumed["status"] == "success" and len(api[2]) == 3, resumed


@pytest.mark.parametrize("filtering", [False, True])
def test_customization_existing_backend(assistant, filtering):
    api, provider = assistant
    api[0].app.state.execution_service.execute("rnaseq.deg", "demo", {"treatment": "treatment", "control": "control"})
    target = "rnaseq.deg" if filtering else "rnaseq.volcano"
    if filtering:
        # Real annotation filtering over synthetic, imported annotations (not a fabricated LLM gene list).
        annotation = api[1].root / "annotation.tsv"
        annotation.write_text("#query\tDescription\tGOs\tKEGG_Pathway\ng_up\tsex differentiation\tGO:0008150\tmap01100\ng_down\tlipid metabolism\tGO:0008150\tmap01100\n")
        assert FileImportService(api[1]).import_file("demo", annotation, "go_mapping", replace=True).status == "succeeded"
    replies(provider, {"request_type": "customization", "target_skill": target}, {
        "mode": "annotation_filter" if filtering else "parameter_override", "target_skill": target,
        "parameters": {"gene_subset": "up", "keywords": ["sex differentiation"], "annotation_sources": ["gene_description"]} if filtering
            else {"color_up": "red", "color_down": "blue", "top_n_labels": 20}})
    body = post(assistant, "筛选性别分化相关上调DEG" if filtering else "把火山图改成红蓝配色，标Top20").json()
    assert body["status"] == "success", body
    assert body["request_type"] == "customization" and body["customization_id"]
    assert [n for n, _ in api[2]].count("rnaseq.deg") == 1
    if filtering:
        assert body["summary"]["n_matched_genes"] == 1


def test_grounding_failure_keeps_artifacts_not_hallucination(assistant):
    workflow_replies(assistant[1], hallucinate=True)
    body = post(assistant).json()
    assert body["status"] == "failed" and body["error_code"] == "LLM_RESPONSE_VALIDATION_FAILED", body
    assert body["artifacts"] and "99999" not in body["response"]
    assert len(assistant[0][2]) == 3


@pytest.mark.parametrize("failed,expected", [("rnaseq.volcano", "partial_success"), ("rnaseq.deg", "failed")])
def test_failed_workflow_branch_is_not_reported_success(assistant, monkeypatch, failed, expected):
    api, provider = assistant
    registry = api[0].app.state.skill_registry
    original = registry.execute
    def execution(name, inputs):
        if name == failed:
            return SkillResult("failed", name, uuid4().hex, error_code="RSCRIPT_FAILED",
                               message="C:/private/failed.R traceback sk-secret-fixture")
        return original(name, inputs)
    monkeypatch.setattr(registry, "execute", execution)
    workflow_replies(provider)
    body = post(assistant).json()
    assert body["status"] == expected, body
    assert "sk-secret-fixture" not in json.dumps(body) and "C:/private" not in json.dumps(body)
    if failed == "rnaseq.deg":
        assert sum(s["status"] == "skipped" for s in body["steps"]) == 2
        assert provider.chat.call_count == 2  # Root failure uses local final response.
    else:
        assert next(s["status"] for s in body["steps"] if s["skill_name"] == "rnaseq.kegg") == "succeeded"


def test_missing_metadata_reports_missing_role(assistant):
    api, provider = assistant
    assert FileImportService(api[1]).remove_file("demo", "metadata").status == "succeeded"
    workflow_replies(provider)
    body = post(assistant).json()
    assert body["status"] == "needs_input" and body["error_code"] == "MISSING_PROJECT_INPUT", body
    assert "metadata" in body["required_fields"] and not api[2]


@pytest.mark.parametrize("name,args,code", [("unknown_tool", {}, "SKILL_NOT_FOUND"),
    ("rnaseq_deg", {"treatment": "treatment", "control": "control", "alpha": 2}, "INVALID_PARAMETER"),
    ("rnaseq_pca", {"output_dir": "results/escape"}, "INVALID_PARAMETER")])
def test_unsafe_model_arguments_never_execute(assistant, name, args, code):
    api, provider = assistant
    replies(provider, {"request_type": "single_skill"})
    provider.request_tool_call.return_value = ToolSchemaResponse("fixture", "mock-only", {"tool_calls": [{
        "id": "call", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}, "tool_calls", {}, .01)
    body = post(assistant).json()
    assert body["status"] == "failed" and body["error_code"] == code, body
    assert not api[2]


@pytest.mark.parametrize("code,http", [("LLM_TIMEOUT", 504), ("LLM_NETWORK_ERROR", 503), ("LLM_RATE_LIMIT", 503)])
def test_provider_failure_safe(assistant, code, http):
    assistant[1].chat.side_effect = DeepSeekError(code, "C:/private sk-do-not-disclose traceback")
    response = post(assistant)
    assert response.status_code == http and response.json()["error"]["code"] == code
    assert "sk-do-not-disclose" not in response.text and "C:/private" not in response.text
    assert not assistant[0][2]


def test_empty_unknown_and_docs(assistant):
    api, provider = assistant
    body = post(assistant, "做PCA", project="empty").json()
    assert body["status"] == "needs_input" and body["error_code"] == "MISSING_PROJECT_INPUT"
    assert body["required_fields"] == ["count_matrix", "metadata"]
    assert post(assistant, project="unknown").status_code == 404
    provider.chat.assert_not_called()
    docs = api[0].get("/openapi.json").json()
    assert "/assistant/analyze" in docs["paths"] and "AssistantResponse" in docs["components"]["schemas"]


@pytest.mark.parametrize("extra", [{"output_dir": "results/x"}, {"conversation_id": "new"}, {"request_id": "../bad"},
    {"source_run_id": "source"}, {"request": "打开 C:\\private\\data.csv"}, {"request": "password=secret"}])
def test_untrusted_request_rejected(assistant, extra):
    assert post(assistant, **({k: v for k, v in extra.items() if k != "request"}),
                text=extra.get("request", "PCA")).status_code == 422
    assert not assistant[0][2]
