"""Opt-in provider planning and R rerendering over explicitly synthetic source fixtures."""

import json
from pathlib import Path
import subprocess
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from bulk_rnaseq.api.main import create_app
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import FileImportService
from test_customization import project
from test_api_assistant import replies


def exercise_api(project, request, monkeypatch, *, real_llm, execute_r, filtering=False):
    manager, snapshot, source_table, annotation = project
    destination = Path(request.config.getoption("--llm-report-dir")) / ("customization_api_" + uuid4().hex)
    destination.mkdir(parents=True)
    if filtering:
        assert FileImportService(manager).import_file("custom_test", annotation, "go_mapping").status == "succeeded"
    record = {"status": "failed", "scope": "Synthetic existing DEG fixture; real provider / rendering as explicitly enabled",
              "real_llm": real_llm, "execute_r": execute_r, "filtering": filtering, "provider_responses": [], "r_scripts": []}
    before = sha256(source_table)
    user_text = "筛选性别分化相关的上调DEG。已有注释使用英文，请用对应英文关键词匹配。" if filtering else "把已有结果画成火山图，上调红色，下调蓝色，标Top20并标注foxl2和sox9。"
    record["user_request"] = user_text
    with TestClient(create_app(workspace_root=manager.root, llm_env_file=Path(request.config.getoption("--llm-env-file")),
                              rscript_executable=request.config.getoption("--rscript"))) as client:
        service = client.app.state.customization_service
        if real_llm:
            factory = service.create_client
            def observed_client():
                provider = factory()
                chat = provider.chat
                def observed(*args, **kwargs):
                    response = chat(*args, **kwargs)
                    record["provider_responses"].append(provider.sanitize_for_audit(response.to_dict()))
                    return response
                monkeypatch.setattr(provider, "chat", observed)
                return provider
            monkeypatch.setattr(service, "create_client", observed_client)
        else:
            provider = Mock()
            provider.sanitize_for_audit.side_effect = lambda value: value
            target = "rnaseq.deg" if filtering else "rnaseq.volcano"
            replies(provider, {"request_type": "customization", "target_skill": target}, {
                "target_skill": target, "mode": "annotation_filter" if filtering else "parameter_override",
                "parameters": {"gene_subset": "up", "keywords": ["sex differentiation"], "annotation_sources": ["gene_description"]}
                    if filtering else {"color_up": "red", "color_down": "blue", "top_n_labels": 20, "label_genes": ["foxl2", "sox9"]}})
            monkeypatch.setattr(service, "create_client", lambda: provider)
        run = subprocess.run
        def render_only(command, **kwargs):
            assert execute_r and not filtering
            assert Path(command[1]).name == "plot_volcano.R"  # No normalization / DEG rerun.
            record["r_scripts"].append(Path(command[1]).name)
            return run(command, **kwargs)
        monkeypatch.setattr(subprocess, "run", render_only)
        try:
            response = client.post("/customizations/plan", json={"project_id": "custom_test",
                "source_run_id": "source-run", "request": user_text})
            record["plan_response"] = response.json()
            assert response.status_code == 200 and response.json()["status"] == "ready", response.text
            prepared = response.json()["plan"]
            decision = prepared["decision"]
            assert prepared["source_run_id"] == "source-run" and not record["r_scripts"]
            if filtering:
                assert decision["mode"] == "annotation_filter" and decision["parameters"]["gene_subset"] == "up"
            else:
                assert decision["mode"] == "parameter_override" and decision["target_skill"] == "rnaseq.volcano"
                assert decision["parameters"]["top_n_labels"] == 20
                assert set(decision["parameters"]["label_genes"]) == {"foxl2", "sox9"}
                assert decision["parameters"]["color_up"].lower() in {"red", "#ff0000"}
                assert decision["parameters"]["color_down"].lower() in {"blue", "#0000ff"}
            if execute_r or filtering:
                monkeypatch.setattr(service, "create_client", Mock(side_effect=AssertionError("Run cannot call LLM")))
                executed = client.post("/customizations/run", json={"project_id": "custom_test", "plan": prepared})
                body = record["run_response"] = executed.json()
                assert executed.status_code == 200 and body["status"] == "succeeded", executed.text
                assert body["source_run_id"] == "source-run" and body["result"]["customization_id"] == body["customization_id"]
                if filtering:
                    assert body["result"]["summary"]["n_matched_genes"] == 1 and not record["r_scripts"]
                else:
                    picture = next(a for a in body["result"]["artifacts"] if a["artifact_id"] == "plot")
                    with Image.open(snapshot.root / picture["relative_ref"]) as image:
                        image.verify()
                    assert record["r_scripts"] == ["plot_volcano.R"]
                assert client.get("/projects/custom_test/results/" + body["result"]["run_id"]).status_code == 200
            assert sha256(source_table) == before
            record["status"] = "passed"
        finally:
            record["request_count"] = len(record["provider_responses"])
            record["usage"] = {key: sum((r.get("usage") or {}).get(key, 0) for r in record["provider_responses"])
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
            write_json(destination / "report.json", record)


@pytest.mark.llm
@pytest.mark.parametrize("filtering", [False, True], ids=["volcano_plan", "annotation_plan_filter"])
def test_real_customization_plan(project, request, monkeypatch, filtering):
    exercise_api(project, request, monkeypatch, real_llm=True, execute_r=False, filtering=filtering)


@pytest.mark.integration
def test_real_r_customization_api(project, request, monkeypatch):
    exercise_api(project, request, monkeypatch, real_llm=False, execute_r=True)


@pytest.mark.llm
@pytest.mark.integration
def test_real_customization_api_closure(project, request, monkeypatch):
    exercise_api(project, request, monkeypatch, real_llm=True, execute_r=True)
