"""Opt-in real planning, optionally followed by real R. Never mock model decisions."""

import csv
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from bulk_rnaseq.api.main import create_app
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workflow.executor import WorkflowExecutor
from bulk_rnaseq.workflow.models import WorkflowResult
from bulk_rnaseq.workspace import FileImportService, ProjectManager


@pytest.mark.llm
@pytest.mark.parametrize("execute_r", [False, pytest.param(True, marks=pytest.mark.integration)], ids=["plan_only", "real_workflow"])
def test_live_workflow_api(execute_r, request, demo_files, monkeypatch):
    destination = Path(request.config.getoption("--llm-report-dir")) / ("workflow_api_" + uuid4().hex)
    destination.mkdir(parents=True)
    manager = ProjectManager(destination / "workspace")
    manager.create_project("demo")
    importer = FileImportService(manager)
    counts, original = demo_files
    # Only the public synthetic demo is used. Group labels are renamed for this test.
    metadata = destination / "metadata.csv"
    with original.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with metadata.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", "condition"])
        writer.writeheader()
        writer.writerows({**row, "condition": {"control": "T", "treatment": "OT"}[row["condition"]]} for row in rows)
    mapping = destination / "synthetic_mapping.tsv"
    mapping.write_text("gene_id\tpathway_id\n" + "".join(f"gene_{i:03d}\tmap01100\n" for i in range(1, 31)), encoding="utf-8")
    for role, source in (("count_matrix", counts), ("metadata", metadata), ("kegg_mapping", mapping)):
        assert importer.import_file("demo", source, role).status == "succeeded"
    user_text = "以T为对照比较OT，画火山图，并对上调基因做KEGG。"
    record = {"scope": "Public synthetic demo; not biological evidence", "request": user_text,
              "execute_r": execute_r, "request_count": 0, "status": "failed"}
    with TestClient(create_app(workspace_root=manager.root, rscript_executable=request.config.getoption("--rscript"),
                              llm_env_file=Path(request.config.getoption("--llm-env-file")))) as client:
        calls = []
        dispatch = client.app.state.skill_registry.execute
        def traced(name, inputs):
            assert execute_r, "Plan-only test cannot execute Skills"
            calls.append(name)
            return dispatch(name, inputs)
        monkeypatch.setattr(client.app.state.skill_registry, "execute", traced)
        create_planner = client.app.state.workflow_service.create_planner
        def observed_planner():
            planner = create_planner()
            chat = planner.client.chat
            def observed_chat(*args, **kwargs):
                record["request_count"] += 1
                response = chat(*args, **kwargs)
                record["llm_response"] = planner.client.sanitize_for_audit(response.to_dict())
                return response
            monkeypatch.setattr(planner.client, "chat", observed_chat)
            return planner
        monkeypatch.setattr(client.app.state.workflow_service, "create_planner", observed_planner)
        def forbidden(*args, **kwargs):
            raise AssertionError("Unexpected analysis or extra LLM request")
        executor = WorkflowExecutor.execute
        monkeypatch.setattr(WorkflowExecutor, "execute", forbidden)
        try:
            response = client.post("/workflow/plan", json={"project_id": "demo", "request": user_text})
            record["plan_response"] = response.json()
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "ready" and calls == []
            steps = {step["skill_name"]: step for step in body["plan"]["steps"]}
            assert set(steps) == {"rnaseq.deg", "rnaseq.volcano", "rnaseq.kegg"}
            deg = steps["rnaseq.deg"]
            assert (deg["parameters"]["treatment"], deg["parameters"]["control"]) == ("OT", "T")
            for name in ("rnaseq.volcano", "rnaseq.kegg"):
                assert body["dependencies"][steps[name]["step_id"]] == [deg["step_id"]]
            assert steps["rnaseq.kegg"]["input_bindings"]["gene_list"]["gene_subset"] == "up"
            if execute_r:
                monkeypatch.setattr(WorkflowExecutor, "execute", executor)
                monkeypatch.setattr(DeepSeekClient, "_request", forbidden)
                started = perf_counter()
                response = client.post("/workflow/run", json={"project_id": "demo", "plan": body["plan"]})
                record["execution_seconds"] = perf_counter() - started
                record["workflow_response"] = response.json()
                assert response.status_code == 200, response.text
                workflow = WorkflowResult.model_validate(response.json())
                assert workflow.status == "succeeded", response.text
                assert sorted(calls) == ["rnaseq.deg", "rnaseq.kegg", "rnaseq.volcano"]
                deg_result = workflow.step_results[deg["step_id"]]
                kegg_step = steps["rnaseq.kegg"]["step_id"]
                assert workflow.step_records[kegg_step]["source_run_id"] == deg_result["run_id"]
                assert workflow.step_records[kegg_step]["upstream_sources"]["gene_list"]["selected_genes"] == deg_result["summary"]["upregulated_genes"]
                assert workflow.step_results[kegg_step]["summary"]["n_enriched"] == 0
                for result in workflow.step_results.values():
                    for path in result["outputs"].values():
                        assert (manager.open_project("demo").root / path).is_file()
            record["status"] = "passed"
        finally:
            record["registry_calls"] = calls
            write_json(destination / "report.json", record)
