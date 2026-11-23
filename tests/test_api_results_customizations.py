"""Free transport + real backend tests. Mock only provider responses / statistical Skills."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from bulk_rnaseq.run_io import sha256
from bulk_rnaseq.workspace import FileImportService
from bulk_rnaseq.workspace.result_manager import ResultManager
from test_api_execution import api
from test_api_assistant import replies
from test_api_workflow import plan as workflow_plan, deg, volcano, kegg


@pytest.fixture
def custom(api, monkeypatch):
    provider = Mock()
    provider.sanitize_for_audit.side_effect = lambda value: value
    monkeypatch.setattr(api[0].app.state.customization_service, "create_client", lambda: provider)
    source = api[0].app.state.execution_service.execute("rnaseq.deg", "demo", {"treatment": "treatment", "control": "control"}).result
    return api, provider, source


def proposal(custom, *, parameters=None, target="rnaseq.volcano", mode="parameter_override", source=True, text=None, **route):
    api, provider, original = custom
    replies(provider, {"request_type": "customization", "target_skill": target, **route}, {
        "mode": mode, "target_skill": target,
        "parameters": parameters if parameters is not None else {"color_up": "red", "color_down": "blue", "top_n_labels": 20},
        "reason": "Unsupported capability" if mode == "unsupported" else ""})
    body = {"project_id": "demo", "request": text or "把火山图改成红蓝，Top20，标注foxl2和sox9；或筛选性别分化相关上调DEG。"}
    if source:
        body["source_run_id"] = original.run_id
    return api[0].post("/customizations/plan", json=body)


def execute(custom, prepared):
    return custom[0][0].post("/customizations/run", json={"project_id": "demo", "plan": prepared})


def test_results_detail_filters_and_lineage(api):
    response = api[0].post("/workflow/run", json={"project_id": "demo", "plan": workflow_plan(deg(), volcano(), kegg())})
    assert response.status_code == 200
    workflow = response.json()
    listing = api[0].get("/projects/demo/results")
    assert listing.status_code == 200, listing.text
    assert len(listing.json()["results"]) == 3 and len(listing.json()["collections"]) == 1
    selected = api[0].get("/projects/demo/results", params={"skill_name": "rnaseq.kegg", "status": "succeeded", "result_type": "Workflow"}).json()
    assert len(selected["results"]) == 1
    identity = selected["results"][0]["run_id"]
    detail = api[0].get("/projects/demo/results/" + identity).json()
    assert detail["source_run_id"] == workflow["step_results"]["deg"]["run_id"]
    assert detail["gene_subset"] == "up" and detail["workflow_id"] == workflow["workflow_id"]
    assert detail["parent_run_ids"] == [detail["source_run_id"]]
    assert all(item["relative_ref"].startswith("results/") for item in detail["artifacts"])
    assert str(api[1].root) not in listing.text and "private-secret" not in listing.text
    assert api[0].get("/projects/demo/results", params={"status": "failed"}).json()["results"] == []
    assert api[0].get("/projects/demo/results", params={"limit": 1}).json()["truncated"]
    assert api[0].get("/projects/empty/results/" + identity).status_code == 404
    assert api[0].get("/projects/demo/results/missing").json()["error"]["code"] == "RESULT_NOT_FOUND"
    assert api[0].get("/projects/missing/results").json()["error"]["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.parametrize("path", ["/projects/%2E%2E%5Cprivate/results", "/projects/C%3Aprivate/results",
    "/projects/demo/results/..%5Csecret", "/projects/demo/results?limit=0", "/projects/demo/results?limit=201"])
def test_path_and_limit_rejected(api, path):
    assert api[0].get(path).status_code == 422


@pytest.mark.parametrize("parameters", [{"color_up": "red", "color_down": "blue"},
    {"top_n_labels": 20, "label_genes": ["foxl2", "sox9"]}])
def test_plan_run_source_immutable_and_replay(custom, parameters):
    api, provider, source = custom
    hashes = {path: sha256(Path(path)) for path in source.outputs.values()}
    response = proposal(custom, parameters=parameters)
    assert response.status_code == 200 and response.json()["status"] == "ready", response.text
    prepared = response.json()["plan"]
    assert prepared["source_run_id"] == source.run_id and len(api[2]) == 1
    assert not ResultManager(api[1], "demo").list_collections("Customization")
    provider.chat.side_effect = AssertionError("Run must not contact an LLM")
    completed = execute(custom, prepared)
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded", completed.text
    body = completed.json()
    assert body["customization_id"] and body["source_run_id"] == source.run_id
    assert body["result"]["customization_id"] == body["customization_id"]
    assert body["result"]["source_run_id"] == source.run_id
    assert body["result"]["parent_run_ids"] == [source.run_id]
    assert api[2][-1][0] == "rnaseq.volcano"
    assert all(sha256(Path(path)) == value for path, value in hashes.items())
    assert execute(custom, prepared).json() == body and len(api[2]) == 2
    listing = api[0].get("/projects/demo/results", params={"result_type": "Customization"}).json()
    assert len(listing["results"]) == len(listing["collections"]) == 1
    assert "private-secret" not in completed.text and str(api[1].root) not in completed.text
    # A separately approved plan yields a separate customization, originals still present.
    new = execute(custom, proposal(custom, parameters=parameters).json()["plan"]).json()
    assert new["customization_id"] != body["customization_id"]


@pytest.mark.parametrize("keyword, expected", [("sex differentiation", 1), ("性别分化", 0)])
def test_annotation_filter_real_deterministic_backend(custom, keyword, expected):
    api, provider, source = custom
    annotation = api[1].root / "annotations.tsv"
    annotation.write_text("#query\tDescription\tGOs\tKEGG_Pathway\ng_up\tsex differentiation\tGO:0008150\tmap01100\ng_down\tlipid metabolism\tGO:0008150\tmap01100\n")
    assert FileImportService(api[1]).import_file("demo", annotation, "go_mapping", replace=True).status == "succeeded"
    response = proposal(custom, target="rnaseq.deg", mode="annotation_filter", parameters={
        "keywords": [keyword], "gene_subset": "up", "annotation_sources": ["gene_description"]})
    assert response.json()["status"] == "ready", response.text
    body = execute(custom, response.json()["plan"]).json()
    assert body["status"] == "succeeded", body
    assert body["result"]["summary"]["n_matched_genes"] == expected
    assert body["result"]["gene_subset"] == "up"
    assert len(api[2]) == 1  # Filter does not rerun DEG or invoke R.


def test_unsupported_no_execution(custom):
    response = proposal(custom, mode="unsupported", parameters={})
    assert response.status_code == 200 and response.json()["status"] == "unsupported"
    assert response.json()["plan"] is None and len(custom[0][2]) == 1


def test_result_reuse_volcano_is_render_only_proposal(custom):
    response = proposal(custom, request_type="result_reuse")
    assert response.status_code == 200 and response.json()["status"] == "ready"
    assert len(custom[0][2]) == 1  # Planning never reruns DEG or renders.
    result = execute(custom, response.json()["plan"])
    assert result.json()["status"] == "succeeded"
    assert [name for name, _ in custom[0][2]] == ["rnaseq.deg", "rnaseq.volcano"]


def test_new_enrichment_is_not_customization(custom):
    response = proposal(custom, request_type="result_reuse", target="rnaseq.kegg")
    assert response.json()["status"] == "needs_input"
    assert response.json()["plan"] is None and custom[1].chat.call_count == 1


def test_ambiguous_then_explicit_source(custom):
    api, provider, source = custom
    api[0].app.state.execution_service.execute("rnaseq.deg", "demo", {"treatment": "treatment", "control": "control"})
    body = proposal(custom, source=False).json()
    assert body["status"] == "needs_input" and body["error_code"] == "AMBIGUOUS_UPSTREAM_RESULT"
    assert len(body["source_options"]) == 2 and provider.chat.call_count == 1
    assert proposal(custom).json()["plan"]["source_run_id"] == source.run_id


def test_no_source_and_cross_project_before_paid_call(custom):
    api, provider, source = custom
    body = {"project_id": "empty", "source_run_id": source.run_id, "request": "改红色"}
    assert api[0].post("/customizations/plan", json=body).status_code == 404
    provider.chat.assert_not_called()
    replies(provider, {"request_type": "customization", "target_skill": "rnaseq.volcano"})
    body.pop("source_run_id")
    result = api[0].post("/customizations/plan", json=body).json()
    assert result["status"] == "needs_input" and result["error_code"] == "MISSING_UPSTREAM_DEPENDENCY"


def test_recent_latest_and_comparison_reuse_shared_resolver(custom, monkeypatch):
    from bulk_rnaseq.workspace.chat_store import ChatStore

    api, _, original = custom
    newer = api[0].app.state.execution_service.execute("rnaseq.deg", "demo", {
        "treatment": "treatment", "control": "control"}).result
    monkeypatch.setattr(ChatStore, "history", lambda self: [{"role": "assistant", "result": {"run_id": original.run_id}}])
    recent = proposal(custom, source=False, source_reference="recent").json()
    assert recent["plan"]["source_run_id"] == original.run_id
    latest = proposal(custom, source=False, source_reference="latest").json()
    assert latest["plan"]["source_run_id"] == newer.run_id
    wrong_direction = proposal(custom, source=False, treatment="control", control="treatment",
        text="把control vs treatment的火山图改成红蓝").json()
    assert wrong_direction["status"] == "needs_input" and wrong_direction["plan"] is None


def test_result_artifact_cannot_reference_outside_project(custom, monkeypatch):
    from dataclasses import replace

    api, _, original = custom
    browser = ResultManager(api[1], "demo")
    run = browser.get_run(original.run_id)
    run.artifacts[0] = replace(run.artifacts[0], relative_path="../../other_project/private.csv")
    monkeypatch.setattr(ResultManager, "get_run", lambda self, identity: run)
    response = api[0].get("/projects/demo/results/" + original.run_id)
    assert response.status_code >= 400
    assert "private.csv" not in response.text and str(api[1].root) not in response.text


@pytest.mark.parametrize("mutation", ["parameters", "project", "unknown", "source", "path"])
def test_plan_tampering_and_cross_project_rejected(custom, mutation):
    api, _, _ = custom
    prepared = proposal(custom).json()["plan"]
    project = "demo"
    if mutation == "parameters":
        prepared["decision"]["parameters"]["top_n_labels"] = 200
    elif mutation == "project":
        project = prepared["project_id"] = "empty"
    elif mutation == "unknown":
        prepared["plan_id"] = "a" * 32
    elif mutation == "source":
        prepared["source_run_id"] = "unknown"
    else:
        prepared["decision"]["parameters"]["output_dir"] = "../escape"
    response = api[0].post("/customizations/run", json={"project_id": project, "plan": prepared})
    assert response.status_code == 422, response.text
    assert len(api[2]) == 1


def test_input_rebinding_rejects_stale_plan(custom):
    api, _, _ = custom
    prepared = proposal(custom).json()["plan"]
    assert FileImportService(api[1]).remove_file("demo", "metadata").status == "succeeded"
    response = execute(custom, prepared)
    assert response.status_code == 409 and response.json()["error"]["code"] == "PROJECT_INPUT_CHANGED"
    assert len(api[2]) == 1


def test_changed_source_never_rerenders(custom):
    api, _, source = custom
    prepared = proposal(custom).json()["plan"]
    Path(source.outputs["deg_csv"]).write_text("changed source fixture")
    response = execute(custom, prepared)
    assert response.status_code == 200 and response.json()["status"] == "failed", response.text
    assert response.json()["error_code"] == "UPSTREAM_ARTIFACT_CHANGED" and len(api[2]) == 1


@pytest.mark.parametrize("params", [{"output_dir": "evil"}, {"shell_command": "whoami"}, {"label_genes": ["invented"]}])
def test_model_unsafe_parameters_not_accepted(custom, params):
    response = proposal(custom, parameters=params)
    assert response.status_code == 502 and response.json()["error"]["code"] == "CUSTOMIZATION_RESPONSE_INVALID"
    assert len(custom[0][2]) == 1
