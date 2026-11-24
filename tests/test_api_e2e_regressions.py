"""Cross-endpoint persistence/failure checks. Synthetic Skill outputs, no network/R."""

from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from bulk_rnaseq.api.main import create_app
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.llm.client import ToolSchemaResponse
from bulk_rnaseq.workspace.result_manager import ResultManager
from test_api_execution import api
from test_api_workflow import deg, volcano, kegg, plan
from test_api_assistant import assistant, replies


@pytest.mark.parametrize("failed,status,skipped", [
    ("rnaseq.volcano", "partial", []), ("rnaseq.deg", "failed", ["kegg", "volcano"])])
def test_failed_branch_is_discoverable_after_app_reload(api, monkeypatch, failed, status, skipped):
    client, manager, calls = api
    execute = client.app.state.skill_registry.execute
    def fail_one(name, inputs):
        if name == failed:
            calls.append((name, inputs))
            return SkillResult("failed", name, uuid4().hex, error_code="RSCRIPT_FAILED", message="Synthetic failure")
        return execute(name, inputs)
    monkeypatch.setattr(client.app.state.skill_registry, "execute", fail_one)
    response = client.post("/workflow/run", json={"project_id": "demo", "plan": plan(deg(), volcano(), kegg())})
    assert response.status_code == 200
    workflow = response.json()
    assert workflow["status"] == status and sorted(workflow["skipped_steps"]) == skipped
    before = client.get("/projects/demo/results").json()
    executions = len(calls)
    # Unit lifecycle reload only. A separate opt-in test actually restarts uvicorn.
    with TestClient(create_app(workspace_root=manager.root)) as reopened:
        assert reopened.get("/projects/demo/results").json() == before
        collection = next(c for c in before["collections"] if c["id"] == workflow["workflow_id"])
        assert collection["status"] == status
        for result in workflow["step_results"].values():
            detail = reopened.get("/projects/demo/results/" + result["run_id"])
            assert detail.status_code == 200 and detail.json()["status"] == result["status"]
    assert len(calls) == executions


def test_results_api_sorts_metadata_time_not_run_id(api, monkeypatch):
    client, manager, _ = api
    body = client.post("/workflow/run", json={"project_id": "demo", "plan": plan(deg())}).json()
    original = ResultManager(manager, "demo").get_run(body["step_results"]["deg"]["run_id"])
    runs = [replace(original, run_id=identity, created_at=stamp, completed_at=None, artifacts=[])
            for identity, stamp in [("zzz_old", "2026-10-10T09:00:00+08:00"),
                                    ("aaa_new", "2026-10-10T02:00:00+00:00")]]
    def discovered(browser):
        browser.runs, browser.collections, browser._loaded = runs, [], True
        return runs
    monkeypatch.setattr(ResultManager, "refresh", discovered)
    listing = client.get("/projects/demo/results")
    assert listing.status_code == 200
    assert [item["run_id"] for item in listing.json()["results"]] == ["aaa_new", "zzz_old"]


def test_cross_project_reuse_does_not_mutate_either_project(api):
    client, _, calls = api
    source = client.post("/workflow/run", json={"project_id": "demo", "plan": plan(deg())}).json()["step_results"]["deg"]
    before = {key: client.get(f"/projects/{key}/results").json() for key in ("demo", "empty")}
    executions = len(calls)
    for name in ("rnaseq.volcano", "rnaseq.kegg"):
        response = client.post(f"/skills/{name}/run", json={"project_id": "empty",
            "parameters": {}, "source_run_id": source["run_id"]})
        assert response.status_code == 404 and response.json()["error"]["code"] == "RESULT_NOT_FOUND"
    assert len(calls) == executions
    assert before == {key: client.get(f"/projects/{key}/results").json() for key in before}


@pytest.mark.parametrize("skill,arguments", [("pca", {}), ("deg", {"treatment": "treatment", "control": "control"})])
def test_assistant_single_result_has_one_identity_and_survives_reload(assistant, skill, arguments):
    api, provider = assistant
    client, manager, calls = api
    replies(provider, {"request_type": "single_skill"})
    provider.request_tool_call.return_value = ToolSchemaResponse("fixture", "mock-only", {"tool_calls": [{
        "id": "call", "type": "function", "function": {"name": "rnaseq_" + skill, "arguments": json.dumps(arguments)}}]},
        "tool_calls", {"total_tokens": 15}, .01)
    body = {"project_id": "demo", "request": "Use control as control, treatment as treatment for differential expression" if skill == "deg" else "Draw PCA", "request_id": str(uuid4())}
    answer = client.post("/assistant/analyze", json=body).json()
    assert answer["status"] == "success", answer
    identity = answer["run_id"]
    detail = client.get("/projects/demo/results/" + identity)
    assert detail.status_code == 200, detail.text
    assert detail.json()["workflow_id"] and detail.json()["step_id"] == "skill"
    assert len([r for r in client.get("/projects/demo/results").json()["results"] if r["run_id"] == identity]) == 1
    assert client.post("/assistant/analyze", json=body).json() == answer
    assert len(calls) == 1
    with TestClient(create_app(workspace_root=manager.root)) as reopened:
        assert reopened.get("/projects/demo/results/" + identity).json() == detail.json()
    if skill == "deg":
        reuse = client.post("/skills/rnaseq.volcano/run", json={"project_id": "demo", "source_run_id": identity, "parameters": {}})
        assert reuse.status_code == 200, reuse.text
        assert reuse.json()["metadata"]["source_run_id"] == identity
        assert [name for name, _ in calls] == ["rnaseq.deg", "rnaseq.volcano"]


def test_conflicting_embedded_result_is_not_silently_merged(api):
    client, manager, calls = api
    result = client.post("/workflow/run", json={"project_id": "demo", "plan": plan(deg())}).json()["step_results"]["deg"]
    source = Path(calls[0][1]["output_dir"]) / "summary.json"
    embedded = json.loads(source.read_text(encoding="utf-8"))
    embedded["summary"]["n_total"] = 999  # Deliberately conflicting OFFLINE fixture, never real research data.
    target = manager.open_project("demo").root / "results/audit_fixture"
    target.mkdir()
    write_json(target / "tool_execution.json", {"request_id": "conflict", "skill_result": embedded})
    response = client.get("/projects/demo/results/" + result["run_id"])
    assert response.status_code == 409 and response.json()["error"]["code"] == "AMBIGUOUS_UPSTREAM_RESULT"
