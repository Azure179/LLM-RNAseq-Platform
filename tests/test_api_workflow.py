"""Workflow HTTP tests use the real parser/validator/executor, synthetic Skill outputs."""

import csv
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from bulk_rnaseq.llm.client import ChatResponse
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.workflow.executor import WorkflowExecutor
from bulk_rnaseq.workflow.models import WorkflowPlan, WorkflowResult
from bulk_rnaseq.workflow.planner import WorkflowPlanner
from bulk_rnaseq.workspace.result_manager import ResultManager
from test_api_execution import api  # Reuse project imports and mock Skill execution.


def deg():
    return {"step_id": "deg", "skill_name": "rnaseq.deg", "parameters": {"treatment": "treatment", "control": "control"}}


def binding(output="deg_csv", **extra):
    return {"source": "workflow_step", "step_id": "deg", "output": output, **extra}


def volcano():
    return {"step_id": "volcano", "skill_name": "rnaseq.volcano", "input_bindings": {"deg_result_file": binding()}}


def kegg():
    return {"step_id": "kegg", "skill_name": "rnaseq.kegg", "input_bindings": {
        "gene_list": binding("filtered_csv", gene_subset="up"), "analysis_genes": binding()}}


def plan(*steps, project="demo"):
    return WorkflowPlan(project_id=project, user_request="Synthetic API workflow fixture", steps=list(steps)).model_dump(mode="json")


def run(api, payload, project="demo"):
    return api[0].post("/workflow/run", json={"project_id": project, "plan": payload})


def planner_fixture(api, monkeypatch, decision):
    client = Mock()
    client.sanitize_for_audit.side_effect = lambda value: value
    client.chat.return_value = ChatResponse("mock", "offline-test", json.dumps(decision), "stop",
                                           {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}, .1)
    service = api[0].app.state.workflow_service
    monkeypatch.setattr(service, "create_planner", lambda: WorkflowPlanner(client, service.registry))
    return client


@pytest.mark.parametrize("steps", [[deg(), volcano(), kegg()],
    [deg(), {"step_id": "pca", "skill_name": "rnaseq.pca"}]])
def test_plan_real_parser_no_execution(api, monkeypatch, steps):
    client = planner_fixture(api, monkeypatch, {"status": "ready", "steps": steps})
    def forbidden(*args, **kwargs):
        raise AssertionError("Planning cannot execute")
    monkeypatch.setattr(WorkflowExecutor, "execute", forbidden)
    response = api[0].post("/workflow/plan", json={"project_id": "demo",
        "request": "Use control as control, compare treatment; PCA or Volcano and KEGG up."})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready" and body["plan"]["workflow_id"]
    assert body["dependencies"]["deg"] == []
    for name in ("volcano", "kegg"):
        if name in body["dependencies"]:
            assert body["dependencies"][name] == ["deg"]
    if "pca" in body["dependencies"]:
        assert body["dependencies"]["pca"] == []
    assert body["usage"]["total_tokens"] == 30
    assert not api[2] and client.chat.call_count == 1
    assert "C:/" not in response.text and str(api[1].root) not in response.text


@pytest.mark.parametrize("status,missing", [("needs_input", ["treatment", "control"]), ("unsupported", [])])
def test_nonready_plan(api, monkeypatch, status, missing):
    planner_fixture(api, monkeypatch, {"status": status, "steps": [], "missing_parameters": missing, "message": "Need input / unsupported"})
    response = api[0].post("/workflow/plan", json={"project_id": "demo", "request": "Describe task"})
    assert response.status_code == 200 and response.json()["status"] == status
    assert response.json()["required_fields"] == missing and response.json()["plan"] is None
    assert not api[2]


def test_run_executor_once_and_canonical_result(api, monkeypatch):
    execute = WorkflowExecutor.execute
    executions = []
    def spy(self, checked, **kwargs):
        executions.append(checked)
        return execute(self, checked, **kwargs)
    monkeypatch.setattr(WorkflowExecutor, "execute", spy)
    response = run(api, plan(deg(), volcano(), kegg()))
    assert response.status_code == 200, response.text
    result = WorkflowResult.model_validate(response.json())
    assert result.status == "succeeded" and len(executions) == 1
    assert [name for name, _ in api[2]].count("rnaseq.deg") == 1
    assert len(api[2]) == 3
    assert result.execution_order.index("deg") < result.execution_order.index("kegg")
    for record in result.step_records.values():
        assert "inputs" not in record and "output_dir" not in record
        assert record["run_id"] and record["started_at"]
        assert ResultManager(api[1], "demo").get_run(record["run_id"]).status == "succeeded"
    assert result.step_records["kegg"]["source_run_id"] == result.step_results["deg"]["run_id"]
    for output in result.step_results.values():
        assert all(not Path(path).is_absolute() for path in output["outputs"].values())
    assert "private-secret" not in response.text
    assert str(api[1].root) not in response.text


@pytest.mark.parametrize("failed,expected,skipped", [("rnaseq.volcano", "partial", []),
    ("rnaseq.deg", "failed", ["kegg", "volcano"])])
def test_partial_and_root_failure_return_200(api, monkeypatch, failed, expected, skipped):
    real = api[0].app.state.skill_registry.execute
    def fake(name, inputs):
        if name == failed:
            api[2].append((name, inputs))
            return SkillResult("failed", name, uuid4().hex, error_code="RSCRIPT_FAILED",
                message="Traceback C:/Users/private sk-PRIVATESECRET", details={"stderr": "private-secret"})
        return real(name, inputs)
    monkeypatch.setattr(api[0].app.state.skill_registry, "execute", fake)
    payload = plan(deg(), volcano(), kegg())
    if failed == "rnaseq.volcano":
        payload["steps"][1]["step_id"] = "a_volcano"  # Force failure before the independent KEGG branch.
    response = run(api, payload)
    assert response.status_code == 200, response.text
    result = WorkflowResult.model_validate(response.json())
    assert result.status == expected and sorted(result.skipped_steps) == skipped
    assert "private-secret" not in response.text and "Traceback" not in response.text
    if skipped:
        assert [name for name, _ in api[2]] == ["rnaseq.deg"]
    else:
        assert "kegg" in result.completed_steps
        assert [name for name, _ in api[2]] == ["rnaseq.deg", "rnaseq.volcano", "rnaseq.kegg"]


def test_historical_reuse_and_project_isolation(api):
    result = run(api, plan(deg())).json()
    source = result["step_results"]["deg"]["run_id"]
    step = kegg()
    for value in step["input_bindings"].values():
        value.update(source="project_result", run_id=source)
        del value["step_id"]
    api[2].clear()
    response = run(api, plan(step))
    assert response.status_code == 200, response.text
    assert [name for name, _ in api[2]] == ["rnaseq.kegg"]
    with Path(api[2][0][1]["gene_list"]).open() as handle:
        assert {row["gene_id"] for row in csv.DictReader(handle)} == {"g_up"}
    assert response.json()["step_records"]["kegg"]["source_run_id"] == source
    api[2].clear()
    other = volcano()
    other["input_bindings"] = {"deg_result_file": {"source": "project_result", "run_id": source, "output": "deg_csv"}}
    response = run(api, plan(other, project="empty"), "empty")
    assert response.status_code == 409 and not api[2]
    path = api[1].open_project("demo").root / result["step_results"]["deg"]["outputs"]["deg_csv"]
    path.write_text("tampered fixture")
    response = run(api, plan(other))
    assert response.status_code == 409 and not api[2]


@pytest.mark.parametrize("case,status,code", [
    ("cycle", 422, "WORKFLOW_CYCLE_DETECTED"), ("output", 422, "INVALID_OUTPUT_BINDING"),
    ("unknown", 404, "SKILL_NOT_FOUND"), ("missing", 409, "MISSING_PROJECT_INPUT"),
    ("upstream", 409, "MISSING_UPSTREAM_DEPENDENCY"), ("mismatch", 422, "INVALID_WORKFLOW_PLAN"),
    ("path", 422, "FORBIDDEN_PARAMETER"), ("host", 422, "FORBIDDEN_PARAMETER"),
    ("group", 422, "INVALID_COMPARISON"), ("project", 404, "PROJECT_NOT_FOUND"),
    ("wrong_branch", 422, "INVALID_EXECUTION_DEPENDENCY")])
def test_invalid_plan_rejected(api, case, status, code):
    payload = plan(deg(), volcano(), kegg())
    project = "demo"
    if case == "cycle":
        payload["steps"][0]["depends_on"] = ["volcano"]
    elif case == "output":
        payload["steps"][1]["input_bindings"]["deg_result_file"]["output"] = "nonexistent"
    elif case == "unknown":
        payload["steps"][0]["skill_name"] = "rnaseq.unknown"
    elif case == "missing":
        project = payload["project_id"] = "empty"
    elif case == "upstream":
        payload = plan({"step_id": "volcano", "skill_name": "rnaseq.volcano"})
    elif case == "mismatch":
        project = "empty"
    elif case == "path":
        payload["steps"][0]["parameters"]["output_dir"] = "C:/private/path"
    elif case == "host":
        payload["steps"][0]["parameters"]["timeout"] = 60
    elif case == "group":
        payload["steps"][0]["parameters"]["treatment"] = "control"
    elif case == "project":
        project = payload["project_id"] = "unknown"
    elif case == "wrong_branch":
        payload["steps"][2]["depends_on"] = ["volcano"]
    response = run(api, payload, project)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text
    assert not api[2]


def test_duplicate_workflow_does_not_rerun(api):
    payload = plan(deg())
    assert run(api, payload).status_code == 200
    response = run(api, payload)
    assert response.status_code == 409 and response.json()["error"]["code"] == "WORKFLOW_ALREADY_EXISTS"
    assert len(api[2]) == 1


@pytest.mark.parametrize("code,status", [("LLM_KEY_MISSING", 503), ("LLM_TIMEOUT", 504),
    ("LLM_RATE_LIMIT", 503), ("LLM_NETWORK_ERROR", 503), ("LLM_PLANNER_JSON_ERROR", 502)])
def test_planner_error_safe(api, monkeypatch, code, status):
    def broken():
        raise DeepSeekError(code, "C:/private sk-SECRET", details={"api_key": "private-secret"})
    monkeypatch.setattr(api[0].app.state.workflow_service, "create_planner", broken)
    response = api[0].post("/workflow/plan", json={"project_id": "demo", "request": "DEG"})
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert "private" not in response.text and not api[2]


def test_malformed_planner_and_unsafe_requests(api, monkeypatch):
    client = planner_fixture(api, monkeypatch, {"status": "ready", "steps": [deg()]})
    client.chat.return_value = ChatResponse("mock", "test", "bad json", "stop", None, .1)
    assert api[0].post("/workflow/plan", json={"project_id": "demo", "request": "DEG"}).status_code == 502
    assert api[0].post("/workflow/plan", json={"project_id": "demo", "request": "read C:/private/file"}).status_code == 422
    assert api[0].post("/workflow/run", json={"project_id": "demo", "request": "DEG"}).status_code == 422
    assert not api[2]


def test_plan_round_trip_and_documentation(api, monkeypatch):
    planner_fixture(api, monkeypatch, {"status": "ready", "steps": [deg(), volcano(), kegg()]})
    response = api[0].post("/workflow/plan", json={"project_id": "demo", "request": "control versus treatment"})
    assert response.status_code == 200, response.text
    response = run(api, response.json()["plan"])
    assert response.status_code == 200, response.text
    document = api[0].get("/openapi.json").json()
    for route in ("/workflow/plan", "/workflow/run"):
        assert document["paths"][route]["post"]["requestBody"]
        assert document["paths"][route]["post"]["responses"]["200"]
    assert document["components"]["schemas"]["WorkflowRunRequest"]["properties"]["plan"]["$ref"].endswith("/WorkflowPlan")


def test_stale_import_and_project_changed_during_planning(api, monkeypatch):
    client = planner_fixture(api, monkeypatch, {"status": "ready", "steps": [deg()]})
    original_response = client.chat.return_value
    def changed(*args, **kwargs):
        assert api[1].open_project("demo")
        from bulk_rnaseq.workspace import FileImportService
        FileImportService(api[1]).remove_file("demo", "go_mapping")
        return original_response
    client.chat.side_effect = changed
    response = api[0].post("/workflow/plan", json={"project_id": "demo", "request": "treatment versus control"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "PROJECT_INPUT_CHANGED"
    metadata = api[1].get_project_context("demo").metadata
    metadata.write_text(metadata.read_text() + "\n")
    response = run(api, plan(deg()))
    assert response.status_code == 409 and response.json()["error"]["code"] == "IMPORTED_FILE_CHANGED"
    assert not api[2]


def test_invalid_model_plan_and_unknown_project_never_execute(api, monkeypatch):
    bad = volcano()
    bad["input_bindings"]["deg_result_file"]["output"] = "nonexistent"
    client = planner_fixture(api, monkeypatch, {"status": "ready", "steps": [deg(), bad]})
    response = api[0].post("/workflow/plan", json={"project_id": "demo", "request": "treatment versus control"})
    assert response.status_code == 502 and response.json()["error"]["code"] == "LLM_PLANNER_PLAN_INVALID"
    response = api[0].post("/workflow/plan", json={"project_id": "unknown", "request": "DEG"})
    assert response.status_code == 404 and client.chat.call_count == 1
    assert not api[2]
