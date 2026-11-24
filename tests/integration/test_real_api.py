"""Opt-in actual HTTP handler -> Registry -> R -> persisted result reuse."""

import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from bulk_rnaseq.api.main import create_app
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workspace import FileImportService, ProjectManager
from bulk_rnaseq.workspace.result_manager import ResultManager


@pytest.mark.integration
def test_real_api_deg_then_volcano(demo_files, request, monkeypatch) -> None:
    def no_llm(*args, **kwargs):
        raise AssertionError("The execution API must not call DeepSeek")
    monkeypatch.setattr("bulk_rnaseq.llm.client.DeepSeekClient._request", no_llm)
    root = Path("outputs/api_tests") / uuid4().hex
    manager = ProjectManager(root / "workspace")
    manager.create_project("demo")
    importer = FileImportService(manager)
    for role, source in zip(("count_matrix", "metadata"), demo_files):
        assert importer.import_file("demo", source, role).status == "succeeded"
    report = {"data": "Public synthetic demo, 30 genes / 6 samples; not research findings", "steps": []}
    with TestClient(create_app(workspace_root=manager.root,
            rscript_executable=request.config.getoption("--rscript"))) as client:
        calls = []
        execute = client.app.state.skill_registry.execute
        def traced_execute(name, inputs):
            calls.append(name)
            return execute(name, inputs)  # Real Registry, no fake output.
        monkeypatch.setattr(client.app.state.skill_registry, "execute", traced_execute)
        started = perf_counter()
        response = client.post("/skills/rnaseq.deg/run", json={"project_id": "demo",
            "parameters": {"treatment": "treatment", "control": "control"}})
        assert response.status_code == 200, response.text
        deg = response.json()
        report["steps"].append({"skill": "rnaseq.deg", "seconds": perf_counter() - started, "result": deg})
        assert deg["summary"]["genes_analyzed"] == 30
        assert calls == ["rnaseq.deg"]
        project = manager.open_project("demo")
        full = project.root / deg["outputs"]["deg_csv"]
        assert full.is_file()
        started = perf_counter()
        response = client.post("/skills/rnaseq.volcano/run", json={"project_id": "demo",
            "source_run_id": deg["run_id"], "parameters": {"top_n_labels": 5}})
        assert response.status_code == 200, response.text
        volcano = response.json()
        report["steps"].append({"skill": "rnaseq.volcano", "seconds": perf_counter() - started, "result": volcano})
        assert calls == ["rnaseq.deg", "rnaseq.volcano"]
        assert volcano["metadata"]["source_run_id"] == deg["run_id"]
        assert any(path.endswith(".png") for path in volcano["outputs"].values())
        for result in (deg, volcano):
            for path in result["outputs"].values():
                assert not Path(path).is_absolute() and (project.root / path).is_file()
            canonical = ResultManager(manager, "demo").get_run(result["run_id"])
            assert canonical.status == "succeeded"
        report["registry_calls"] = calls
        report["status"] = "passed"
        write_json(root / "report.json", report)
    # Recreate the app: persisted result identity and paths survive process state.
    with TestClient(create_app(workspace_root=manager.root)) as client:
        from bulk_rnaseq.workspace.result_resolver import ResultResolver
        resolved = ResultResolver(client.app.state.execution_service.manager, "demo").find_compatible_result(
            skill_name="rnaseq.deg", run_id=deg["run_id"], required_outputs=("deg_csv",))
        assert resolved.status == "resolved"
        assert json.loads((root / "report.json").read_text(encoding="utf-8"))["registry_calls"] == calls
