"""Free HTTP mocks only. Fixtures are protocol examples, not LLM evaluation results."""

from dataclasses import replace
import json
import subprocess

import httpx
import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.workflow import WorkflowContext, WorkflowPlan
from bulk_rnaseq.workflow.planner import PlannerDecision, WorkflowPlanner


@pytest.fixture(autouse=True)
def forbid_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Planner may not access executors or run subprocesses")
    monkeypatch.setattr(SkillRegistry, "execute", forbidden)
    monkeypatch.setattr(SkillRegistry, "get_executor", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def context():
    return WorkflowContext("private project", frozenset({"count_matrix", "metadata", "go_mapping", "kegg_mapping"}),
                           {"rnaseq.pca": {"group_column": "condition"}, "rnaseq.heatmap": {"group_column": "condition"}})


def step_ref(output="deg_csv", subset=None):
    binding = {"source": "workflow_step", "step_id": "deg", "output": output}
    if subset:
        binding["gene_subset"] = subset
    return binding


def proposed():
    return {"status": "ready", "steps": [
        {"step_id": "deg", "skill_name": "rnaseq.deg", "parameters": {"treatment": "OT", "control": "T"}},
        {"step_id": "volcano", "skill_name": "rnaseq.volcano", "input_bindings": {"deg_result_file": step_ref()}},
        {"step_id": "kegg", "skill_name": "rnaseq.kegg", "input_bindings": {
            "gene_list": step_ref("filtered_csv", "up"), "analysis_genes": step_ref()}},
    ]}


def mock_planner(payload, *, finish_reason="stop", http_status=200):
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(http_status, json={"id": "mock-response", "model": "mock-model", "choices": [
            {"finish_reason": finish_reason, "message": {"role": "assistant", "content":
                payload if isinstance(payload, str) else json.dumps(payload)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})
    client = DeepSeekClient(DeepSeekConfig(api_key="mock-secret-key"), transport=httpx.MockTransport(handle))
    return WorkflowPlanner(client, create_default_registry()), requests


def test_real_client_mock_json_request_and_branch(context):
    planner, requests = mock_planner(proposed())
    result = planner.plan("以 T 为对照比较 OT，画火山图，对上调基因做 KEGG", context=context, available_groups=["OT", "T", "O"])
    assert isinstance(result.plan, WorkflowPlan)
    assert result.validation.valid
    assert result.validation.dependencies == {"deg": [], "volcano": ["deg"], "kegg": ["deg"]}
    assert result.plan.steps[2].input_bindings["gene_list"].gene_subset == "up"
    assert result.response.usage["total_tokens"] == 150
    assert len(requests) == 1
    body = requests[0]
    assert body["response_format"] == {"type": "json_object"}
    assert "tools" not in body
    prompt = body["messages"][0]["content"]
    assert len(prompt) < 40000
    assert "private project" not in prompt
    assert "mock-secret-key" not in json.dumps(body)
    assert "rscript_executable" not in prompt and "output_dir" not in prompt
    assert {step.skill_name for step in result.plan.steps} <= planner.catalog.keys()
    assert result.plan.project_id == "private project"


@pytest.mark.parametrize("status,missing", [("needs_input", ["treatment", "control"]), ("unsupported", [])])
def test_nonready_no_partial_plan(context, status, missing):
    planner, requests = mock_planner({"status": status, "missing_parameters": missing, "message": "请补充或选择支持任务"})
    result = planner.plan("做差异分析", context=context)
    assert result.decision.status == status
    assert result.plan is None and result.validation is None
    assert len(requests) == 1


@pytest.mark.parametrize("raw,code", [
    ("not JSON", "LLM_PLANNER_JSON_ERROR"),
    ('{"status":"ready","status":"unsupported"}', "LLM_PLANNER_JSON_ERROR"),
    ('{"status":NaN}', "LLM_PLANNER_JSON_ERROR"),
    ("[]", "LLM_PLANNER_SCHEMA_ERROR"),
    ({"status": "ready", "steps": []}, "LLM_PLANNER_SCHEMA_ERROR"),
    ({"status": "needs_input", "message": "missing"}, "LLM_PLANNER_SCHEMA_ERROR"),
    ({"status": "unsupported", "message": "No", "steps": proposed()["steps"]}, "LLM_PLANNER_SCHEMA_ERROR"),
    ({"status": "unsupported", "message": "No", "extra": "ignored?"}, "LLM_PLANNER_SCHEMA_ERROR"),
    ({"status": "unsupported", "message": "Read C:/private/file"}, "LLM_PLANNER_UNSAFE_RESPONSE"),
])
def test_bad_responses_no_repair(context, raw, code):
    planner, requests = mock_planner(raw)
    with pytest.raises(DeepSeekError) as caught:
        planner.plan("分析", context=context)
    assert caught.value.code == code
    assert caught.value.response is not None
    assert len(requests) == 1


def test_incomplete_response(context):
    planner, requests = mock_planner(proposed(), finish_reason="length")
    with pytest.raises(DeepSeekError, match="incomplete") as caught:
        planner.plan("以 T 为对照比较 OT", context=context)
    assert caught.value.code == "LLM_PLANNER_INCOMPLETE"
    assert len(requests) == 1


@pytest.mark.parametrize("mutation,error_code", [
    (lambda p: p["steps"][0].update(skill_name="rnaseq.seurat"), "LLM_PLANNER_PLAN_INVALID"),
    (lambda p: p["steps"][0]["parameters"].update(alpha=2), "LLM_PLANNER_PLAN_INVALID"),
    (lambda p: p["steps"][0]["parameters"].update(output_dir="results/fake"), "LLM_PLANNER_PLAN_INVALID"),
    (lambda p: p["steps"][0]["parameters"].update(rscript_executable="command"), "LLM_PLANNER_PLAN_INVALID"),
    (lambda p: p["steps"][2].update(depends_on=["volcano"]), "LLM_PLANNER_PLAN_INVALID"),
    (lambda p: p["steps"][0].update(depends_on=["volcano"]), "LLM_PLANNER_PLAN_INVALID"),
    (lambda p: p["steps"][2]["input_bindings"]["gene_list"].update(output="deg_up"), "LLM_PLANNER_PLAN_INVALID"),
    (lambda p: p["steps"][2].update(parameters={"gene_subset": "up"}), "LLM_PLANNER_PLAN_INVALID"),
    (lambda p: p["steps"].append(p["steps"][0]), "LLM_PLANNER_SCHEMA_ERROR"),
    (lambda p: p["steps"][0]["parameters"].update(treatment="invented"), "LLM_PLANNER_PARAMETER_ERROR"),
    (lambda p: p["steps"][0]["parameters"].update(control="OT"), "LLM_PLANNER_PARAMETER_ERROR"),
])
def test_invalid_plan_locally_rejected(context, mutation, error_code):
    payload = proposed()
    mutation(payload)
    planner, requests = mock_planner(payload)
    with pytest.raises(DeepSeekError) as caught:
        planner.plan("以 T 为对照比较 OT", context=context)
    assert caught.value.code == error_code
    assert len(requests) == 1


def test_unoffered_history_rejected(context):
    payload = proposed()
    payload["steps"][1]["input_bindings"]["deg_result_file"] = {
        "source": "project_result", "run_id": "guessed-id", "output": "deg_csv"}
    planner, _ = mock_planner(payload)
    with pytest.raises(DeepSeekError) as caught:
        planner.plan("以 T 为对照比较 OT", context=context)
    assert caught.value.code == "LLM_PLANNER_BINDING_ERROR"


def test_missing_mapping_not_auto_filled(context):
    planner, _ = mock_planner(proposed())
    with pytest.raises(DeepSeekError) as caught:
        planner.plan("以 T 为对照比较 OT", context=replace(context, available_inputs=frozenset({"count_matrix", "metadata"})))
    assert "MISSING_PROJECT_INPUT" in caught.value.details["error_codes"]


@pytest.mark.parametrize("text", ["读 C:\\private\\counts.csv", "读 /home/private/data", "读 /file", "读 \\\\server\\private"])
def test_local_path_input_not_sent(context, text):
    planner, requests = mock_planner(proposed())
    with pytest.raises(DeepSeekError) as caught:
        planner.plan(text, context=context)
    assert caught.value.code == "LLM_PLANNER_INPUT_ERROR"
    assert requests == []


@pytest.mark.parametrize("parameters", [{"output_dir": "private"}, {"group_column": "C:/private/data"}, {"group_column": "mock-secret-key"}])
def test_private_context_not_sent(context, parameters):
    planner, requests = mock_planner(proposed())
    with pytest.raises(DeepSeekError):
        planner.plan("PCA", context=replace(context, confirmed_parameters={"rnaseq.pca": parameters}))
    assert requests == []


def test_unconfirmed_group_not_in_inventory(context):
    planner, _ = mock_planner(proposed())
    with pytest.raises(DeepSeekError) as caught:
        planner.plan("以 T 为对照比较 OT", context=context, available_groups=["A", "B"])
    assert caught.value.code == "LLM_PLANNER_PARAMETER_ERROR"


def test_transport_error_preserved(context):
    planner, requests = mock_planner({}, http_status=429)
    with pytest.raises(DeepSeekError) as caught:
        planner.plan("PCA", context=context)
    assert caught.value.code == "LLM_RATE_LIMIT"
    assert len(requests) == 1


@pytest.mark.parametrize("groups,parameters", [
    (["A", "B"], {"treatment": "OT", "control": "T"}),
    (["OT", "T"], {"treatment": "OT", "control": "OT"}),
])
def test_inconsistent_confirmed_comparison_not_sent(context, groups, parameters):
    planner, requests = mock_planner(proposed())
    with pytest.raises(DeepSeekError) as caught:
        planner.plan("比较组别", context=replace(context, confirmed_parameters={"rnaseq.deg": parameters}), available_groups=groups)
    assert caught.value.code == "LLM_PLANNER_INPUT_ERROR"
    assert requests == []


def test_bad_host_project_key_not_sent(context):
    planner, requests = mock_planner(proposed())
    with pytest.raises(DeepSeekError):
        planner.plan("分析", context=replace(context, project_id="../escape"))
    assert requests == []


def test_confirmed_direction_without_rewriting_model_parameters(context):
    payload = proposed()
    payload["steps"][0]["parameters"] = {}
    planner, requests = mock_planner(payload)
    context = replace(context, confirmed_parameters={"rnaseq.deg": {"treatment": "OT", "control": "T"}})
    result = planner.plan("比较 OT 和 T", context=context, available_groups=["OT", "T"])
    assert result.plan.steps[0].parameters == {}  # Preserve the actual proposed plan.
    assert result.validation.resolved_parameters["deg"]["control"] == "T"
    assert "CONFIRMED_PARAMETERS" in requests[0]["messages"][0]["content"]


def test_existing_schema_and_manifest_defaults(context):
    planner, _ = mock_planner(proposed())
    schema = PlannerDecision.model_json_schema()
    assert schema["$defs"]["WorkflowStep"]["properties"]["input_bindings"]
    assert planner.catalog["rnaseq.deg"]["parameters"]["properties"]["alpha"]["default"] == .05
    assert planner.catalog["rnaseq.heatmap"]["parameters"]["properties"]["top_n"]["default"] == 50
    assert set(planner.catalog) == {"rnaseq." + name for name in ("deg", "pca", "volcano", "heatmap", "go", "kegg")}


def test_pca_deg_independence(context):
    payload = proposed()
    payload["steps"] = [payload["steps"][0], {"step_id": "pca", "skill_name": "rnaseq.pca"}]
    planner, _ = mock_planner(payload)
    result = planner.plan("以 T 为对照比较 OT，同时画 PCA", context=context)
    assert result.validation.dependencies == {"deg": [], "pca": []}
