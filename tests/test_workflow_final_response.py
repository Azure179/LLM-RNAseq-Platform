"""Synthetic summary fixtures; never execute Skills, R, Planner or paid API."""

import json
import subprocess

import httpx
import pytest

from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.result_formatter import METRICS
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.workflow import WorkflowExecutor, WorkflowResult
from bulk_rnaseq.workflow.final_response import (WorkflowFinalResponseBuilder, WorkflowFinalResponseResult,
                                                format_workflow_result_for_llm)

KEY = "sk-unit-fixture-workflow-summary-only"


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Summary must not execute Skills/Workflow/R")
    for target, method in ((SkillRegistry, "execute"), (WorkflowExecutor, "execute"),
                           (subprocess, "run"), (subprocess, "Popen")):
        monkeypatch.setattr(target, method, forbidden)


def workflow(case="success"):
    names = {"deg": "rnaseq.deg", "volcano": "rnaseq.volcano", "kegg": "rnaseq.kegg"}
    if case == "roots":
        names = {"pca": "rnaseq.pca", "deg": "rnaseq.deg"}
    if case == "partial_skipped":
        names["pca"] = "rnaseq.pca"
    failed = ["deg"] if case in {"failed", "partial_skipped"} else ["volcano"] if case == "partial" else []
    skipped = ["volcano", "kegg"] if case in {"failed", "partial_skipped"} else []
    completed = [s for s in names if s not in failed + skipped]
    results, records = {}, {}
    for step, name in names.items():
        status = "succeeded" if step in completed else "failed" if step in failed else "skipped"
        result = None
        if status == "succeeded":
            summary = {key: 4 for key in METRICS[name]}
            if step == "deg":
                summary.update(genes_analyzed=30, genes_with_pvalue=30, significant_genes=18,
                               upregulated_genes=8, downregulated_genes=10)
            if case == "empty" and step == "kegg":
                summary["n_enriched"] = 0
            result = SkillResult("succeeded", name, f"run-{step}", summary=summary,
                inputs={"parameters": {"treatment": "OT", "control": "T", "alpha": .05, "lfc_threshold": 1}},
                outputs={"filtered_csv" if step == "deg" else "plot": "C:/PRIVATE/result.csv"},
                metadata={"processes": [{"stderr": "LARGE-TABLE-SENTINEL"}]})
        elif status == "failed":
            result = SkillResult("failed", name, f"run-{step}", error_code="TEST_ERROR", message="Synthetic safe error")
        records[step] = {"status": status, "skill_name": name, "run_id": result.run_id if result else None,
                         "outputs": result.outputs if result else {}, "blocked_by": ["deg"] if status == "skipped" else []}
        if result:
            results[step] = result.to_dict()
    if "kegg" in completed:
        binding = {"source": "workflow_step", "step_id": "deg", "output": "filtered_csv", "gene_subset": "up"}
        records["kegg"].update(input_bindings={"gene_list": binding},
                               upstream_sources={"gene_list": {**binding, "run_id": "run-deg"}})
    return WorkflowResult(workflow_id="fixture-workflow", status="failed" if case == "failed" else "partial" if case in {"partial", "partial_skipped"} else "succeeded",
        user_request="Summarize existing results; unit-test fixture only", execution_order=list(names),
        completed_steps=completed, failed_steps=failed, skipped_steps=skipped, step_results=results, step_records=records)


def mock_client(source, mutate=lambda text: text, *, finish="stop", http_status=200):
    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert "tools" not in payload and "tool_choice" not in payload
        assert payload["response_format"] == {"type": "json_object"}
        serialized = json.dumps(payload)
        assert "C:/PRIVATE" not in serialized and "LARGE-TABLE-SENTINEL" not in serialized
        answer = mutate("。".join(format_workflow_result_for_llm(source).facts) + "。")
        return httpx.Response(http_status, json={"id": "fixture", "model": "fixture-model", "choices": [{
            "message": {"role": "assistant", "content": json.dumps({"final_response": answer}, ensure_ascii=False)},
            "finish_reason": finish}], "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50}})
    return DeepSeekClient(DeepSeekConfig(KEY), transport=httpx.MockTransport(respond)), calls


@pytest.mark.parametrize("case", ["success", "partial", "failed", "empty", "roots", "partial_skipped"])
def test_required_cases(case):
    source = workflow(case)
    before = source.model_dump()
    client, calls = mock_client(source)
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.status == ("failed" if case == "failed" else "succeeded"), result.model_dump()
    assert result.workflow_status == source.status
    assert result.request_count == len(calls) == (0 if case == "failed" else 1)
    assert result.grounded and source.model_dump() == before
    assert WorkflowFinalResponseResult.model_validate_json(result.model_dump_json()) == result
    if case == "partial":
        assert "部分成功" in result.final_response and "volcano（rnaseq.volcano）执行失败" in result.final_response
        assert "Synthetic safe error" in result.final_response
    if case == "failed":
        assert "已跳过" in result.final_response and result.usage is None
        assert "Synthetic safe error" in result.final_response
    if case == "empty":
        assert "分析成功，但在当前阈值下未检测到显著富集结果" in result.final_response
    if case in {"success", "partial", "empty"}:
        assert "上游已筛选DEG中的上调基因" in result.final_response


@pytest.mark.parametrize("extra", ["新增9999个基因", "TP53显著上调", "富集于map00010", "细胞增殖增强", "分析已成功完成", "并非"])
def test_unsupported_claim_rejected(extra):
    source = workflow("partial")
    client, _ = mock_client(source, lambda text: text + extra)
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.error_code == "LLM_RESPONSE_VALIDATION_FAILED"
    assert result.final_response == "" and not result.grounded
    assert result.usage["total_tokens"] == 50


@pytest.mark.parametrize("mutation", ["swapped", "status", "missing", "duplicate"])
def test_changed_facts_rejected(mutation):
    source = workflow("partial")
    def mutate(text):
        if mutation == "swapped":
            return text.replace("上调基因数为8", "上调基因数为10").replace("下调基因数为10", "下调基因数为8")
        if mutation == "status":
            return text.replace("volcano（rnaseq.volcano）执行失败", "volcano（rnaseq.volcano）执行成功")
        if mutation == "missing":
            return text.replace("工作流部分成功，未全部完成", "")
        return text + "工作流部分成功，未全部完成"
    client, _ = mock_client(source, mutate)
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.error_code == "LLM_RESPONSE_VALIDATION_FAILED" and not result.final_response


def test_warnings_local_preserved_not_transmitted():
    source = workflow()
    source.warnings = ["Workflow warning C:/PRIVATE/diagnostics"]
    source.step_results["deg"]["warnings"] = [{"code": "LOW_REPLICATES", "message": "Only 2 replicates.", "details": {}}]
    client, calls = mock_client(source)
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.status == "succeeded"
    assert "Only 2 replicates." in result.final_response and "Workflow warning" in result.final_response
    assert "Only 2 replicates." not in json.dumps(calls)


@pytest.mark.parametrize("mutation", ["count", "status", "run_id", "subset", "source", "order"])
def test_malformed_evidence_no_api(mutation):
    source = workflow()
    if mutation == "count":
        source.step_results["deg"]["summary"]["upregulated_genes"] = 900
    elif mutation in {"status", "run_id"}:
        source.step_records["deg"][mutation] = "wrong"
    elif mutation == "subset":
        source.step_records["kegg"]["input_bindings"]["gene_list"]["gene_subset"] = "down"
    elif mutation == "source":
        source.step_records["kegg"]["upstream_sources"]["gene_list"]["run_id"] = "different-run"
    else:
        source.execution_order = ["deg"]
    client, calls = mock_client(source)
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.error_code in {"INVALID_WORKFLOW_RESULT", "INVALID_SKILL_RESULT"} and not calls


@pytest.mark.parametrize("finish,status,expected", [("length", 200, "LLM_RESPONSE_VALIDATION_FAILED"), ("stop", 429, "LLM_RATE_LIMIT")])
def test_api_failure_no_retry(finish, status, expected):
    source = workflow()
    client, calls = mock_client(source, finish=finish, http_status=status)
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.error_code == expected and len(calls) == 1 and not result.final_response


def test_oversize_not_silently_truncated():
    source = workflow()
    source.warnings = ["x" * 16001]
    client, calls = mock_client(source)
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.error_code == "WORKFLOW_SUMMARY_TOO_LARGE" and not calls


def test_secret_in_context_rejected():
    source = workflow()
    source.step_results["deg"]["inputs"]["parameters"]["treatment"] = KEY
    client, calls = mock_client(source)
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.error_code == "INVALID_WORKFLOW_RESULT" and not calls
    assert KEY not in result.model_dump_json()


def test_skipped_cannot_be_described_as_success():
    source = workflow("partial_skipped")
    client, _ = mock_client(source, lambda text: text.replace("已跳过，未执行分析", "执行成功"))
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.error_code == "LLM_RESPONSE_VALIDATION_FAILED" and not result.final_response


def test_aggregated_warning_not_duplicated():
    source = workflow()
    source.step_results["deg"]["warnings"] = [{"code": "WARNING", "message": "Keep this warning", "details": {"count": 2}}]
    source.warnings = ["deg: WARNING: Keep this warning"]
    client, _ = mock_client(source)
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.final_response.count("Keep this warning") == 1
    assert "count=2" in result.final_response


@pytest.mark.parametrize("content", ['not JSON', '[]', '{"final_response":null}',
    '{"final_response":"x","final_response":"y"}', '{"final_response":NaN}', '{"final_response":"x","extra":1}'])
def test_malformed_model_json_rejected(content, monkeypatch):
    from bulk_rnaseq.llm.client import ChatResponse
    source = workflow()
    client, _ = mock_client(source)
    monkeypatch.setattr(client, "chat", lambda *args, **kwargs: ChatResponse("fixture", "fixture-model", content, "stop", None, 0.0))
    result = WorkflowFinalResponseBuilder(client).build(source)
    assert result.error_code == "LLM_RESPONSE_VALIDATION_FAILED" and not result.final_response
