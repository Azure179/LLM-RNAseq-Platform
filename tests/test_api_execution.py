"""Real HTTP/backend binding with synthetic Skill outputs. No paid API or R."""

import csv
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from bulk_rnaseq.api.main import create_app
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.run_io import write_json, sha256
from bulk_rnaseq.workspace import ProjectManager, FileImportService
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.result_resolver import ResultResolver


@pytest.fixture
def api(tmp_path, demo_files, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No DeepSeek or R in default API tests")
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("bulk_rnaseq.llm.client.DeepSeekClient._request", forbidden)
    manager = ProjectManager(tmp_path / "workspace")
    manager.create_project("demo")
    manager.create_project("empty")
    service = FileImportService(manager)
    for role, path in zip(("count_matrix", "metadata"), demo_files):
        assert service.import_file("demo", path, role).status == "succeeded"
    mapping = tmp_path / "mapping.tsv"
    mapping.write_text("gene_id\tpathway_id\ng_up\tmap01100\ng_down\tmap01100\n")
    assert service.import_file("demo", mapping, "kegg_mapping").status == "succeeded"
    go = tmp_path / "go.tsv"
    go.write_text("gene_id\tgo_id\ng_up\tGO:0008150\ng_down\tGO:0008150\n")
    assert service.import_file("demo", go, "go_mapping").status == "succeeded"
    with TestClient(create_app(workspace_root=manager.root)) as client:
        calls = []
        def execute(name, inputs):
            calls.append((name, inputs))
            root = Path(inputs["output_dir"])
            root.mkdir()
            outputs = {}
            if name == "rnaseq.deg":
                for key in ("deg_csv", "filtered_csv"):
                    target = root / (key + ".csv")
                    target.write_text("gene_id,log2FoldChange,pvalue,padj\ng_up,2,0.001,0.01\ng_down,-2,0.001,0.01\n")
                    outputs[key] = str(target)
            else:
                target = root / "test_output.csv"
                target.write_text("synthetic_test_output\n")
                outputs["plot_data"] = str(target)
            result = SkillResult("succeeded", name, uuid4().hex, outputs=outputs,
                inputs={"parameters": inputs}, summary={"n_total": 2},
                metadata={"environment": {"API_KEY": "private-secret"}, "processes": [{"stderr": "private-secret"}],
                          "provenance": {"artifact_sha256": {Path(path).name: sha256(Path(path)) for path in outputs.values()}}})
            write_json(root / "summary.json", result.to_dict())
            return result
        monkeypatch.setattr(client.app.state.skill_registry, "execute", execute)
        yield client, manager, calls


def run(api, skill="deg", params=None, project="demo", **extra):
    return api[0].post("/skills/rnaseq." + skill + "/run", json={"project_id": project,
        "parameters": {"treatment": "treatment", "control": "control"} if params is None else params, **extra})


def test_deg_once_and_discoverable(api):
    response = run(api)
    assert response.status_code == 200, response.text
    result = SkillResult.from_dict(response.json())
    assert len(api[2]) == 1 and api[2][0][0] == "rnaseq.deg"
    assert result.status == "succeeded" and result.run_id
    assert result.metadata["project_id"] == "demo" and result.metadata["created_at"]
    browser = ResultManager(api[1], "demo")
    assert browser.get_run(result.run_id).skill_name == "rnaseq.deg"
    assert ResultResolver(api[1], "demo").find_compatible_result(skill_name="rnaseq.deg").result.run_id == result.run_id
    for path in result.outputs.values():
        assert path.startswith("results/") and (browser.project_root / path).is_file()
    assert "private-secret" not in response.text and str(api[1].root) not in response.text
    assert "_legacy" not in result.to_dict()


@pytest.mark.parametrize("skill,project,params,status,code", [
    ("unknown", "demo", {}, 404, "SKILL_NOT_FOUND"),
    ("gsea", "demo", {}, 409, "SKILL_UNAVAILABLE"),
    ("deg", "absent", None, 404, "PROJECT_NOT_FOUND"),
    ("deg", "empty", None, 409, "MISSING_PROJECT_INPUT"),
    ("deg", "demo", {"treatment": "control", "control": "control"}, 422, "INVALID_COMPARISON"),
    ("deg", "demo", {"treatment": "absent", "control": "control"}, 422, "INVALID_COMPARISON"),
    ("volcano", "demo", {}, 409, "MISSING_UPSTREAM_DEPENDENCY"),
    ("deg", "demo", {"treatment": "treatment"}, 422, "INVALID_PARAMETER"),
    ("deg", "demo", {"treatment": "treatment", "control": "control", "alpha": 2}, 422, "INVALID_PARAMETER"),
])
def test_rejected_before_registry(api, skill, project, params, status, code):
    response = run(api, skill, params, project)
    assert response.status_code == status, response.text
    body = response.json()
    assert body.get("error", {}).get("code", body.get("error_code")) == code
    assert not api[2]


@pytest.mark.parametrize("parameter", ["output_dir", "project_root", "rscript_executable", "Rscript", "api_key",
    "count_matrix_path", "metadata_path", "timeout", "run_id", "command", "runtime", "gene_list"])
def test_forbidden_parameters(api, parameter):
    response = run(api, params={"treatment": "treatment", "control": "control", parameter: "C:/private/secret"})
    expected = "INVALID_REQUEST" if parameter == "gene_list" else "FORBIDDEN_PARAMETER"
    assert response.status_code == 422 and response.json()["error"]["code"] == expected
    assert not api[2] and "C:/private" not in response.text


@pytest.mark.parametrize("extra", [{"output_dir": "x"}, {"run_id": "x"}, {"source_run_id": "../another"}])
def test_forbidden_envelope(api, extra):
    assert run(api, **extra).status_code == 422
    assert not api[2]


def test_volcano_existing_deg_not_rerun(api):
    deg = run(api).json()
    api[2].clear()
    response = run(api, "volcano", {"treatment": "treatment", "control": "control", "top_n_labels": 20})
    assert response.status_code == 200, response.text
    assert [name for name, _ in api[2]] == ["rnaseq.volcano"]
    assert response.json()["metadata"]["source_run_id"] == deg["run_id"]
    source = api[1].open_project("demo").root / deg["outputs"]["deg_csv"]
    assert Path(api[2][0][1]["deg_result_file"]) == source


@pytest.mark.parametrize("subset,expected", [("up", {"g_up"}), ("down", {"g_down"}), ("all", {"g_up", "g_down"})])
def test_kegg_real_binding(api, subset, expected):
    deg = run(api).json()
    api[2].clear()
    response = run(api, "kegg", {"treatment": "treatment", "control": "control", "gene_subset": subset})
    assert response.status_code == 200, response.text
    assert [name for name, _ in api[2]] == ["rnaseq.kegg"]
    inputs = api[2][0][1]
    with Path(inputs["gene_list"]).open() as handle:
        assert {row["gene_id"] for row in csv.DictReader(handle)} == expected
    assert Path(inputs["analysis_genes"]) == api[1].open_project("demo").root / deg["outputs"]["deg_csv"]
    provenance = response.json()["metadata"]["upstream_sources"]["gene_list"]
    assert provenance["source_run_id"] == deg["run_id"] and provenance["gene_subset"] == subset


def test_ambiguity_selection_isolation_and_direction(api):
    first, second = run(api).json(), run(api).json()
    api[2].clear()
    response = run(api, "volcano", {})
    assert response.status_code == 409
    candidates = response.json()["error"]["details"]["candidates"]
    assert {r["run_id"] for r in candidates} == {first["run_id"], second["run_id"]}
    assert not api[2]
    assert run(api, "volcano", {}, "empty", source_run_id=first["run_id"]).status_code == 404
    assert run(api, "volcano", {"treatment": "control", "control": "treatment"}).status_code == 409
    response = run(api, "volcano", {}, source_run_id=first["run_id"])
    assert response.status_code == 200 and response.json()["metadata"]["source_run_id"] == first["run_id"]
    assert [name for name, _ in api[2]] == ["rnaseq.volcano"]


def test_changed_source_and_import_rejected(api):
    deg = run(api).json()
    api[2].clear()
    project = api[1].open_project("demo")
    (project.root / deg["outputs"]["deg_csv"]).write_text("altered fixture")
    assert run(api, "volcano", {}, source_run_id=deg["run_id"]).status_code == 404
    metadata = api[1].get_project_context("demo").metadata
    metadata.write_text(metadata.read_text() + "\n")
    response = run(api)
    assert response.status_code == 409 and response.json()["error"]["code"] == "IMPORTED_FILE_CHANGED"
    assert not api[2]


def test_failed_r_result_and_no_private_text(api, monkeypatch):
    calls = []
    def failure(name, inputs):
        calls.append(name)
        return SkillResult("failed", name, uuid4().hex, error_code="RSCRIPT_FAILED",
            message="Traceback C:/Users/private sk-PRIVATESECRET", details={"stderr": "private-secret"})
    monkeypatch.setattr(api[0].app.state.skill_registry, "execute", failure)
    response = run(api)
    assert response.status_code == 502 and response.json()["error"]["code"] == "RSCRIPT_FAILED"
    assert calls == ["rnaseq.deg"]
    assert all(value not in response.text for value in ("C:/Users", "sk-PRIVATE", "Traceback", "private-secret"))
    assert ResultManager(api[1], "demo").get_run(response.json()["error"]["details"]["run_id"]).status == "failed"


def test_openapi_business_parameters_only(api):
    document = api[0].get("/openapi.json").json()
    operation = document["paths"]["/skills/{skill_name}/run"]["post"]
    assert "requestBody" in operation
    for status in (200, 404, 409, 422, 500, 502, 503, 504):
        assert str(status) in operation["responses"]
    for schema in operation["x-skill-parameters"].values():
        assert not {"output_dir", "rscript_executable", "project_root", "run_id", "count_matrix_path"} & schema["properties"].keys()
    assert "gene_subset" in operation["x-skill-parameters"]["rnaseq.kegg"]["properties"]
    assert "_legacy" not in document["components"]["schemas"]["SkillResultResponse"]["properties"]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_rejected(api, value):
    response = api[0].post("/skills/rnaseq.deg/run", content=(
        '{"project_id":"demo","parameters":{"treatment":"treatment","control":"control","alpha":' + value + '}}'),
        headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert not api[2]


def test_generic_internal_error_is_safe(api, monkeypatch):
    def failure(*args, **kwargs):
        raise RuntimeError("Traceback C:/Users/private sk-PRIVATESECRET")
    monkeypatch.setattr(api[0].app.state.execution_service, "execute", failure)
    response = run(api)
    assert response.status_code == 500 and response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert all(value not in response.text for value in ("C:/Users", "sk-PRIVATE", "Traceback"))


def test_success_warning_sanitization(api, monkeypatch):
    execute = api[0].app.state.skill_registry.execute
    def warning_result(*args, **kwargs):
        payload = execute(*args, **kwargs).to_dict()
        payload["warnings"] = [{"code": "TEST_WARNING", "message": "Check C:/Users/private/input.csv",
            "details": {"api_key": "private-secret", "normal": 2}}]
        payload["summary"]["private_path"] = "/home/private/input.csv"
        return SkillResult.from_dict(payload)
    monkeypatch.setattr(api[0].app.state.skill_registry, "execute", warning_result)
    response = run(api)
    assert response.status_code == 200, response.text
    assert response.json()["warnings"][0]["details"] == {"normal": 2}
    assert response.json()["summary"]["private_path"] == "[redacted]"
    assert all(value not in response.text for value in ("C:/Users", "/home/private", "private-secret"))


@pytest.mark.parametrize("skill", ["pca", "heatmap", "go"])
def test_other_registered_skills_use_host_inputs(api, skill):
    source = run(api).json() if skill != "pca" else None
    api[2].clear()
    parameters = {"group_column": "condition"} if skill in {"pca", "heatmap"} else {}
    response = run(api, skill, parameters, **({"source_run_id": source["run_id"]} if source else {}))
    assert response.status_code == 200, response.text
    assert [name for name, _ in api[2]] == ["rnaseq." + skill]
    assert Path(api[2][0][1]["output_dir"]).is_relative_to(api[1].root)
    if source:
        assert response.json()["metadata"]["source_run_id"] == source["run_id"]


@pytest.mark.parametrize("code,status", [("NO_MAPPED_GENES", 422), ("ONTOLOGY_INFORMATION_REQUIRED", 422),
    ("NO_SIGNIFICANT_GENES", 422), ("AMBIGUOUS_INPUT_BINDING", 409), ("RSCRIPT_TIMEOUT", 504),
    ("RSCRIPT_NOT_FOUND", 503), ("UNEXPECTED_NEW_CODE", 500)])
def test_error_mapping_is_explicit(code, status):
    from bulk_rnaseq.api.execution import http_error
    assert http_error(code)[0] == status
