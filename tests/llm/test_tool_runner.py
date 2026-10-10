"""Offline execution boundary tests. HTTP and executor are explicit fixtures."""

import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.execution_context import ExecutionContext
from bulk_rnaseq.llm.tool_runner import ToolRunner
from bulk_rnaseq.result_schema import SkillResult


KEY = "sk-fixture-runner-credential"


def call(name: str = "rnaseq_deg", args: str = '{"treatment":"OT","control":"T"}') -> dict:
    return {"id": "fixture-call", "type": "function", "function": {"name": name, "arguments": args}}


@pytest.fixture
def host(tmp_path: Path) -> ExecutionContext:
    (tmp_path / "counts.csv").touch()
    (tmp_path / "meta.csv").touch()
    return ExecutionContext(project_root=tmp_path, output_root=Path("runs"), bindings={
        "rnaseq.deg": {"count_matrix_path": "counts.csv", "metadata_path": "meta.csv"},
        "rnaseq.pca": {"count_matrix": "counts.csv", "metadata": "meta.csv"}},
        confirmed_parameters={"rnaseq.pca": {"group_column": "condition"}})


def runner(message: dict, monkeypatch: pytest.MonkeyPatch, finish: str | None = None) -> tuple[ToolRunner, Mock]:
    registry = create_default_registry()
    execute = Mock(return_value=SkillResult("succeeded", "rnaseq.deg", "fixture-run"))
    monkeypatch.setattr(registry, "execute", execute)
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Offline runner tests must not launch subprocesses")
    monkeypatch.setattr("subprocess.run", forbidden)
    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["tool_choice"] == "auto" and len(payload["tools"]) == 6
        assert payload["messages"][0]["role"] == "system"
        assert "counts.csv" not in payload["messages"][0]["content"]
        return httpx.Response(200, json={"id": "fixture-response", "model": "fixture-model", "choices": [{
            "message": {"role": "assistant", **message},
            "finish_reason": finish or ("tool_calls" if message.get("tool_calls") else "stop")}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    client = DeepSeekClient(DeepSeekConfig(KEY), transport=httpx.MockTransport(respond))
    return ToolRunner(client, registry), execute


def test_single_execution_and_context_injection(host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, execute = runner({"content": None, "tool_calls": [call()]}, monkeypatch)
    result = agent.run("OT vs T", host)
    assert result.status == "succeeded" and result.skill_result["schema_version"] == "2.0"
    execute.assert_called_once()
    name, inputs = execute.call_args.args
    assert name == "rnaseq.deg" and inputs["count_matrix_path"] == host.project_root / "counts.csv"
    assert inputs["output_dir"].is_relative_to(host.project_root / "runs")
    assert inputs["rscript_executable"] == Path("Rscript")
    assert "output_dir" not in result.arguments
    audit = host.project_root / "runs" / result.request_id / "tool_execution.json"
    assert json.loads(audit.read_text(encoding="utf-8"))["status"] == "succeeded"


@pytest.mark.parametrize("args", ['{"alpha":2,"treatment":"OT","control":"T"}',
    '{"treatment":"OT","control":"T","output_dir":"C:/bad"}',
    '{"treatment":"OT","control":"T","rscript_executable":"cmd"}',
    '{"treatment":"OT","control":"T","api_key":"bad"}',
    '{"treatment":"OT","control":"OT"}', '{"treatment":"invented","control":"T"}'])
def test_invalid_arguments_never_execute(args: str, host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, execute = runner({"tool_calls": [call(args=args)]}, monkeypatch)
    result = agent.run("OT vs T", host)
    assert result.status == "failed" and result.stage == "argument_validation"
    execute.assert_not_called()


@pytest.mark.parametrize("args", ["not JSON", "[]", '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e400}'])
def test_invalid_json(args: str, host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, execute = runner({"tool_calls": [call(args=args)]}, monkeypatch)
    result = agent.run("OT vs T", host)
    assert result.error_code == "INVALID_TOOL_ARGUMENTS_JSON"
    execute.assert_not_called()


@pytest.mark.parametrize("calls,code", [([call(), call()], "MULTIPLE_TOOL_CALLS_NOT_SUPPORTED"),
                                       ([call("rnaseq_unknown")], "LLM_TOOL_UNKNOWN_NAME")])
def test_call_selection_rejected(calls: list, code: str, host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, execute = runner({"tool_calls": calls}, monkeypatch)
    assert agent.run("OT vs T", host).error_code == code
    execute.assert_not_called()


@pytest.mark.parametrize("status,skill,missing", [("needs_input", "rnaseq.deg", ["treatment", "control"]),
                                                 ("unsupported", None, [])])
def test_no_tool_decision(status: str, skill: str | None, missing: list, host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, execute = runner({"content": json.dumps({"status": status, "skill_name": skill,
        "missing_parameters": missing, "message": "fixture explanation"})}, monkeypatch)
    assert agent.run("request", host).status == status
    execute.assert_not_called()


def test_no_tool_free_text_not_locally_classified(host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, execute = runner({"content": "Please provide more information"}, monkeypatch)
    assert agent.run("request", host).error_code == "NO_TOOL_SELECTED"
    execute.assert_not_called()


@pytest.mark.parametrize("name", ["volcano", "heatmap", "go", "kegg"])
def test_missing_upstream_no_hidden_pipeline(name: str, host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    args = '{"group_column":"condition"}' if name == "heatmap" else "{}"
    agent, execute = runner({"tool_calls": [call("rnaseq_" + name, args)]}, monkeypatch)
    result = agent.run("use condition", host)
    assert result.status == "needs_input" and result.error_code == "MISSING_DEPENDENCY"
    assert result.missing_parameters
    execute.assert_not_called()


def test_dry_run_and_confirmed_pca(host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, execute = runner({"tool_calls": [call("rnaseq_pca", "{}")]}, monkeypatch)
    result = agent.run("PCA", host, execute=False)
    assert result.status == "validated" and result.validated_arguments["group_column"] == "condition"
    execute.assert_not_called()


@pytest.mark.parametrize("path", ["../outside.csv", ".env"])
def test_disallowed_context_path_before_api(path: str, host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    host.bindings["rnaseq.deg"]["count_matrix_path"] = path
    agent, execute = runner({"tool_calls": [call()]}, monkeypatch)
    request = Mock()
    monkeypatch.setattr(agent.client, "request_tool_call", request)
    result = agent.run("OT vs T", host)
    assert result.error_code == "CONTEXT_PATH_NOT_ALLOWED" and result.request_count == 0
    request.assert_not_called()
    execute.assert_not_called()


def test_failed_skill_result_preserved(host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, execute = runner({"tool_calls": [call()]}, monkeypatch)
    execute.return_value = SkillResult("failed", "rnaseq.deg", None, error_code="RSCRIPT_FAILED", message="fixture failure")
    result = agent.run("OT vs T", host)
    assert result.status == "failed" and result.stage == "skill_execution"
    assert result.skill_result["error_code"] == "RSCRIPT_FAILED"


def test_secret_not_logged(host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, execute = runner({"tool_calls": [call()]}, monkeypatch)
    result = agent.run(KEY, host)
    assert result.stage == "llm_request" and result.status == "failed"
    execute.assert_not_called()
    assert KEY not in json.dumps(result.model_dump())
    for path in (host.project_root / "runs" / result.request_id).iterdir():
        assert KEY not in path.read_text(encoding="utf-8")


def test_missing_bound_file_stops_before_registry(host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    host.bindings["rnaseq.deg"]["metadata_path"] = "absent.csv"
    agent, execute = runner({"tool_calls": [call()]}, monkeypatch)
    result = agent.run("OT vs T", host)
    assert result.stage == "context_injection" and result.error_code == "CONTEXT_FILE_NOT_FOUND"
    execute.assert_not_called()


def test_context_cannot_override_business_fields(host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    host.bindings["rnaseq.deg"]["treatment"] = "override"
    agent, execute = runner({"tool_calls": [call()]}, monkeypatch)
    result = agent.run("OT vs T", host)
    assert result.error_code == "INVALID_EXECUTION_CONTEXT" and result.request_count == 0
    execute.assert_not_called()


def test_background_constraint_rejected_before_api(host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    host.bindings["rnaseq.go"] = {"gene_list": "counts.csv", "gene_to_go_mapping": "counts.csv",
                                   "universe": "counts.csv", "analysis_genes": "counts.csv"}
    agent, execute = runner({"tool_calls": [call("rnaseq_go", "{}")]}, monkeypatch)
    result = agent.run("GO", host)
    assert result.stage == "context_injection" and result.error_code == "INVALID_EXECUTION_CONTEXT"
    assert result.request_count == 0
    execute.assert_not_called()


def test_incomplete_response_never_executes(host: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, execute = runner({"content": "partial"}, monkeypatch, finish="length")
    result = agent.run("OT vs T", host)
    assert result.error_code == "LLM_INCOMPLETE_RESPONSE"
    execute.assert_not_called()
