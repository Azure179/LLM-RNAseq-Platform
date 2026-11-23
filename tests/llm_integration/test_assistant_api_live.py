"""Opt-in real Assistant classification/planning and complete LLM + R closure."""

import csv
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from bulk_rnaseq.api.main import create_app
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workspace import FileImportService, ProjectManager


@pytest.mark.llm
@pytest.mark.parametrize("execute_r", [False, pytest.param(True, marks=pytest.mark.integration)], ids=["classification_plan", "full_e2e"])
def test_live_assistant_api(execute_r, request, demo_files, monkeypatch):
    destination = Path(request.config.getoption("--llm-report-dir")) / ("assistant_api_" + uuid4().hex)
    destination.mkdir(parents=True)
    manager = ProjectManager(destination / "workspace")
    manager.create_project("demo")
    counts, original = demo_files
    metadata = destination / "metadata.csv"
    with original.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with metadata.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", "condition"])
        writer.writeheader()
        writer.writerows({**row, "condition": {"control": "T", "treatment": "OT"}[row["condition"]]} for row in rows)
    mapping = destination / "synthetic_mapping.tsv"
    mapping.write_text("gene_id\tpathway_id\n" + "".join(f"gene_{i:03d}\tmap01100\n" for i in range(1, 31)), encoding="utf-8")
    importer = FileImportService(manager)
    for role, path in (("count_matrix", counts), ("metadata", metadata), ("kegg_mapping", mapping)):
        assert importer.import_file("demo", path, role).status == "succeeded"
    record = {"scope": "Public synthetic demo, real DeepSeek and optional real R; not biological evidence",
              "execute_r": execute_r, "request_count": 0, "provider_responses": [], "status": "failed"}
    text = "以T为对照比较OT，画火山图，并对上调基因做KEGG。"
    with TestClient(create_app(workspace_root=manager.root, rscript_executable=request.config.getoption("--rscript"),
                              llm_env_file=Path(request.config.getoption("--llm-env-file")))) as client:
        service = client.app.state.assistant_service
        create = service.create_client
        def observed_client():
            provider = create()
            chat = provider.chat
            def observed(*args, **kwargs):
                record["request_count"] += 1
                response = chat(*args, **kwargs)
                record["provider_responses"].append(provider.sanitize_for_audit(response.to_dict()))
                return response
            monkeypatch.setattr(provider, "chat", observed)
            return provider
        monkeypatch.setattr(service, "create_client", observed_client)
        dispatch = service.single.registry.execute
        calls = []
        def execution(name, inputs):
            assert execute_r, "Classification/plan test must not execute a Skill"
            calls.append(name)
            return dispatch(name, inputs)
        monkeypatch.setattr(service.single.registry, "execute", execution)
        run = service.workflow.run
        def observed_run(project_id, plan):
            record["plan"] = plan.model_dump(mode="json")
            if not execute_r:
                # Test-only stop at the validated plan boundary, not a production dry-run flag.
                raise SkillError("TEST_EXECUTION_DISABLED", "Plan-only test: intentionally stopped before execution.")
            workflow = run(project_id, plan)
            record["workflow"] = workflow.model_dump(mode="json")
            return workflow
        monkeypatch.setattr(service.workflow, "run", observed_run)
        try:
            response = client.post("/assistant/analyze", json={"project_id": "demo", "request": text})
            body = record["response"] = response.json()
            assert response.status_code == 200, response.text
            assert body["request_type"] == "workflow", body
            steps = {step["skill_name"]: step for step in record["plan"]["steps"]}
            assert set(steps) == {"rnaseq.deg", "rnaseq.volcano", "rnaseq.kegg"}
            deg = steps["rnaseq.deg"]
            assert (deg["parameters"]["treatment"], deg["parameters"]["control"]) == ("OT", "T")
            assert steps["rnaseq.kegg"]["input_bindings"]["gene_list"]["gene_subset"] == "up"
            if execute_r:
                assert body["status"] == "success" and body["final_response_status"] == "succeeded", body
                assert sorted(calls) == ["rnaseq.deg", "rnaseq.kegg", "rnaseq.volcano"]
                workflow = record["workflow"]
                deg_result = workflow["step_results"][deg["step_id"]]
                kegg_id = steps["rnaseq.kegg"]["step_id"]
                assert workflow["step_records"][kegg_id]["upstream_sources"]["gene_list"]["source_run_id"] == deg_result["run_id"]
                assert workflow["step_records"][kegg_id]["upstream_sources"]["gene_list"]["selected_genes"] == deg_result["summary"]["upregulated_genes"]
                assert workflow["step_results"][kegg_id]["summary"]["n_enriched"] == 0
                assert "上调基因数为9" in body["response"] and "未检测到显著富集结果" in body["response"]
                for artifact in body["artifacts"]:
                    assert (manager.open_project("demo").root / artifact["path"]).is_file()
            else:
                assert not calls and body["error_code"] == "TEST_EXECUTION_DISABLED"
            record["status"] = "passed"
        finally:
            record["registry_calls"] = calls
            record["usage"] = {key: sum((r.get("usage") or {}).get(key, 0) for r in record["provider_responses"])
                               for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
            write_json(destination / "report.json", record)
