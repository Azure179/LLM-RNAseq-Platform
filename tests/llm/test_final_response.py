"""Free HTTP/executor fixtures, explicitly NOT evidence of real model/statistics results."""

import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.execution_context import ExecutionContext
from bulk_rnaseq.llm.final_response import FinalResponseBuilder, FinalResponseResult, validate_grounding
from bulk_rnaseq.llm.result_formatter import METRICS, format_skill_result_for_llm
from bulk_rnaseq.llm.single_request import SingleRequestRunner, combine_usage
from bulk_rnaseq.llm.tool_runner import ToolExecutionResult
from bulk_rnaseq.result_schema import SkillResult


KEY = "sk-fixture-only-final-response"


def fixture_result(skill: str = "rnaseq.deg", *, empty: bool = False) -> SkillResult:
    summary = {key: 4 for key in METRICS[skill]}
    if skill == "rnaseq.deg":
        summary.update(genes_analyzed=30, genes_with_pvalue=30, significant_genes=18,
                       upregulated_genes=9, downregulated_genes=9)
    if skill == "rnaseq.pca":
        summary.update(pc1_variance=85.9088594454781, pc2_variance=4.76667160361138)
    if empty:
        summary["n_enriched"] = 0
    return SkillResult("succeeded", skill, "fixture-run", summary=summary,
                       inputs={"parameters": {"treatment": "OT", "control": "T", "alpha": .05, "lfc_threshold": 1.0}},
                       outputs={"summary_json": "C:/private/summary.json"},
                       metadata={"processes": [{"stderr": "HUGE-TABLE-SENTINEL"}]})


def client_for(result: SkillResult, *, content: str | None = None, finish: str = "stop",
               http_status: int = 200) -> tuple[DeepSeekClient, list[dict]]:
    requests = []
    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        assert "tools" not in payload and "tool_choice" not in payload
        assert payload["response_format"] == {"type": "json_object"}
        assert "HUGE-TABLE-SENTINEL" not in json.dumps(payload) and "C:/private" not in json.dumps(payload)
        answer = content
        if answer is None:
            # Mock fixture only. Real integration tests never use this constructor.
            answer = json.dumps({"final_response": "分析已成功完成。" + "；".join(format_skill_result_for_llm(result).facts) + "。"}, ensure_ascii=False)
        return httpx.Response(http_status, json={"id": "fixture", "model": "fixture-model", "choices": [{
            "message": {"role": "assistant", "content": answer}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})
    return DeepSeekClient(DeepSeekConfig(KEY), transport=httpx.MockTransport(respond)), requests


@pytest.mark.parametrize("skill", list(METRICS))
def test_all_formatters_and_summary(skill: str) -> None:
    source = fixture_result(skill)
    client, calls = client_for(source)
    result = FinalResponseBuilder(client).build("fixture request", skill, source)
    assert result.status == "succeeded" and result.grounded and len(calls) == 1
    assert result.usage["total_tokens"] == 30 and result.request_count == 1
    assert FinalResponseResult.model_validate_json(result.model_dump_json()) == result


def test_pca_display_rounding_and_source_unchanged() -> None:
    source = fixture_result("rnaseq.pca")
    before = source.to_dict()
    assert "PC1解释方差为85.91%" in format_skill_result_for_llm(source).facts
    assert source.to_dict() == before


def test_missing_parameters_are_not_defaults() -> None:
    context = format_skill_result_for_llm(fixture_result("rnaseq.go"))
    assert "当前结果未提供padj阈值（小于等于）" in context.facts


def test_warning_preserved_even_when_model_does_not_echo_it() -> None:
    payload = fixture_result().to_dict()
    payload["warnings"] = [{"code": "LOW_REPLICATES", "message": "Only 2 biological replicates detected per group.", "details": {"count": 2}}]
    source = SkillResult.from_dict(payload)
    client, _ = client_for(source)
    result = FinalResponseBuilder(client).build("fixture request", source.skill_name, source)
    assert result.status == "succeeded" and payload["warnings"][0]["message"] in result.final_response
    assert "count=2" in result.final_response


def test_failed_no_api() -> None:
    client, calls = client_for(fixture_result(), content="must not request")
    source = SkillResult("failed", "rnaseq.deg", None, error_code="INVALID_COUNT_MATRIX", message="Counts must be non-negative integers.")
    result = FinalResponseBuilder(client).build("fixture", source.skill_name, source)
    assert result.status == "failed" and result.error_code == "INVALID_COUNT_MATRIX"
    assert "分析未执行成功" in result.final_response and not calls and result.request_count == 0


@pytest.mark.parametrize("skill", ["rnaseq.go", "rnaseq.kegg"])
def test_empty_is_success(skill: str) -> None:
    source = fixture_result(skill, empty=True)
    client, _ = client_for(source)
    result = FinalResponseBuilder(client).build("fixture", skill, source)
    assert result.status == "succeeded" and "未检测到显著富集结果" in result.final_response


@pytest.mark.parametrize("mutation", ["missing", "negative", "boolean", "nonfinite", "inconsistent", "bad_schema", "mismatch"])
def test_malformed_no_api(mutation: str) -> None:
    payload = fixture_result().to_dict()
    if mutation == "missing":
        del payload["summary"]["upregulated_genes"]
    elif mutation == "bad_schema":
        payload["schema_version"] = "wrong"
    elif mutation == "mismatch":
        payload["skill_name"] = "rnaseq.pca"
    else:
        payload["summary"]["upregulated_genes"] = {"negative": -1, "boolean": True, "nonfinite": float("nan"), "inconsistent": 100}[mutation]
    client, calls = client_for(fixture_result())
    result = FinalResponseBuilder(client).build("fixture", "rnaseq.deg", payload)
    assert result.error_code == "INVALID_SKILL_RESULT" and not calls


@pytest.mark.parametrize("content", ['not JSON', '[]', '{"final_response":"ok","final_response":"bad"}',
                                      '{"final_response":NaN}', '{"final_response":1e400}', '{"final_response":null}'])
def test_malformed_model_response(content: str) -> None:
    source = fixture_result()
    client, _ = client_for(source, content=content)
    result = FinalResponseBuilder(client).build("fixture", source.skill_name, source)
    assert result.error_code == "LLM_RESPONSE_VALIDATION_FAILED" and not result.final_response
    assert result.usage["total_tokens"] == 30


@pytest.mark.parametrize("extra", ["共1234个基因。", "富集于GO:0008150。", "TP53显著上调。", "细胞增殖显著增强。", "分析失败。", "并非"])
def test_unverified_claim_rejected(extra: str) -> None:
    context = format_skill_result_for_llm(fixture_result())
    with pytest.raises(ValueError):
        validate_grounding("；".join(context.facts) + extra, context)


def test_swapped_counts_rejected_even_when_both_numbers_are_verified() -> None:
    payload = fixture_result().to_dict()
    payload["summary"].update(upregulated_genes=8, downregulated_genes=10)
    context = format_skill_result_for_llm(SkillResult.from_dict(payload))
    swapped = "；".join(context.facts).replace("上调基因数为8", "上调基因数为10").replace("下调基因数为10", "下调基因数为8")
    with pytest.raises(ValueError):
        validate_grounding(swapped, context)


@pytest.mark.parametrize("finish,status,code", [("length", 200, "LLM_RESPONSE_VALIDATION_FAILED"), ("stop", 401, "LLM_AUTH_ERROR")])
def test_llm_failure_no_fake_success(finish: str, status: int, code: str) -> None:
    source = fixture_result()
    client, calls = client_for(source, finish=finish, http_status=status)
    result = FinalResponseBuilder(client).build("fixture", source.skill_name, source)
    assert result.error_code == code and result.final_response == "" and len(calls) == 1


def test_usage_unknown_not_zero() -> None:
    assert combine_usage({"total_tokens": 10}, None)["total_tokens"] is None
    assert combine_usage({"total_tokens": 10}, {"total_tokens": 20})["total_tokens"] == 30


@pytest.mark.parametrize("summary_ok", [True, False])
def test_complete_chain_two_requests_one_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, summary_ok: bool) -> None:
    for filename in ("counts.csv", "meta.csv"):
        (tmp_path / filename).touch()
    context = ExecutionContext(project_root=tmp_path, output_root=Path("runs"), bindings={
        "rnaseq.deg": {"count_matrix_path": "counts.csv", "metadata_path": "meta.csv"}})
    registry = create_default_registry()
    execute = Mock(return_value=fixture_result())
    monkeypatch.setattr(registry, "execute", execute)
    calls = []
    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            assert "tools" in payload
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "fixture-tool", "type": "function",
                "function": {"name": "rnaseq_deg", "arguments": '{"treatment":"OT","control":"T"}'}}]}
            finish = "tool_calls"
        else:
            assert "tools" not in payload
            message = {"role": "assistant", "content": json.dumps({"final_response": "；".join(format_skill_result_for_llm(fixture_result()).facts) if summary_ok else "伪造结论"})}
            finish = "stop"
        return httpx.Response(200, json={"id": "fixture-response", "model": "fixture-model", "choices": [{
            "message": message, "finish_reason": finish}], "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})
    client = DeepSeekClient(DeepSeekConfig(KEY), transport=httpx.MockTransport(respond))
    result = SingleRequestRunner(client, registry).run("OT vs T", context)
    assert result.status == ("succeeded" if summary_ok else "failed")
    execute.assert_called_once()
    assert len(calls) == 2 and result.request_count == 2 and result.usage["total_tokens"] == 60
    assert result.execution.status == "succeeded"  # Summary failure never discards/reruns the successful analysis.
    report = tmp_path / "runs" / result.execution.request_id / "final_response.json"
    assert report.is_file() and KEY not in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("status", ["needs_input", "unsupported", "failed"])
def test_selection_failure_never_summarizes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    execution = ToolExecutionResult(status=status, request_id="fixture", message="Local decision", request_count=1)
    monkeypatch.setattr("bulk_rnaseq.llm.single_request.ToolRunner.run", Mock(return_value=execution))
    client, calls = client_for(fixture_result())
    result = SingleRequestRunner(client, create_default_registry()).run("fixture", ExecutionContext(project_root=tmp_path, output_root=Path("runs")))
    assert result.status == status and not calls and result.final is None


def test_report_failure_does_not_get_masked_by_successful_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution = ToolExecutionResult(status="failed", request_id="fixture", error_code="TOOL_REPORT_IO_ERROR",
                                    skill_result=fixture_result().to_dict())
    monkeypatch.setattr("bulk_rnaseq.llm.single_request.ToolRunner.run", Mock(return_value=execution))
    client, calls = client_for(fixture_result())
    result = SingleRequestRunner(client, create_default_registry()).run("fixture", ExecutionContext(project_root=tmp_path, output_root=Path("runs")))
    assert result.error_code == "TOOL_REPORT_IO_ERROR" and not calls


def test_result_schema_refuses_fake_success() -> None:
    with pytest.raises(ValueError):
        FinalResponseResult(status="succeeded", request_id="fixture")


def test_zero_effect_selected_rows_do_not_change_existing_deg_contract() -> None:
    payload = fixture_result().to_dict()
    payload["inputs"]["parameters"]["lfc_threshold"] = 0
    payload["summary"].update(significant_genes=19, upregulated_genes=9, downregulated_genes=9)
    context = format_skill_result_for_llm(SkillResult.from_dict(payload))
    assert "差异基因数为19" in context.facts


def test_secret_in_context_rejected_before_request() -> None:
    payload = fixture_result().to_dict()
    payload["inputs"]["parameters"]["treatment"] = KEY
    client, calls = client_for(fixture_result())
    result = FinalResponseBuilder(client).build("fixture", "rnaseq.deg", payload)
    assert result.error_code == "INVALID_SKILL_RESULT" and not calls
    assert KEY not in result.model_dump_json()


def test_oversize_warning_not_silently_dropped() -> None:
    payload = fixture_result().to_dict()
    payload["warnings"] = [{"code": "LONG", "message": "x" * 11000}]
    client, calls = client_for(fixture_result())
    result = FinalResponseBuilder(client).build("fixture", "rnaseq.deg", payload)
    assert result.error_code == "INVALID_SKILL_RESULT" and not calls


def test_complete_cli_invalid_config_no_api(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from bulk_rnaseq.llm.run_complete import main
    assert main(["fixture", "--context", str(tmp_path / "missing.json")]) == 1
    assert "INVALID_EXECUTION_CONTEXT" in caplog.text
