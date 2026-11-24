"""Use normal import, HTTP, chat and workflow entry points on disposable projects."""
import csv
import json
from uuid import uuid4

import pytest

from bulk_rnaseq.workspace import FileImportService
from bulk_rnaseq.workspace.result_resolver import ResultResolver
from bulk_rnaseq.workflow.executor import WorkflowExecutor
from test_api_execution import api, run
from test_workflow_chat_ui import host, run_workflow


def replace(manager, key, role, directory):
    current = getattr(manager.get_project_context(key), role)
    bundle = json.loads((current.parent.parent / "import.json").read_bytes())
    path = directory / bundle["original_filename"]
    with current.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if role == "count_matrix":
        rows[1][1] = str(int(rows[1][1]) + 1)
    else:
        other = next(row for row in rows[2:] if row[1] != rows[1][1])
        other[1], rows[1][1] = rows[1][1], other[1]
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    result = FileImportService(manager).replace_file(key, path, role)
    assert result.status == "succeeded", result


@pytest.mark.parametrize("role", ["count_matrix", "metadata"])
def test_same_filename_new_version_rejected_then_new_deg_reusable(api, tmp_path, role):
    old = run(api).json()["run_id"]
    replace(api[1], "demo", role, tmp_path)
    for child in ("volcano", "go", "kegg"):
        response = run(api, child, {})
        assert (response.status_code, response.json()["error"]["code"]) == (409, "STALE_DEG_RESULT")
    assert [n for n, _ in api[2]] == ["rnaseq.deg"]
    historical = run(api, "volcano", {}, source_run_id=old).json()
    assert historical["metadata"]["upstream_sources"]["deg_result_file"]["reuse_scope"] == "historical_run"
    new = run(api).json()["run_id"]
    assert new != old
    child = run(api, "volcano", {"top_n_labels": 2}).json()
    assert child["metadata"]["parent_run_ids"] == [new]
    assert child["metadata"]["upstream_sources"]["deg_result_file"]["reuse_scope"] == "current_input_snapshot"


def test_original_artifact_tamper_and_unrelated_mapping(api, tmp_path):
    run(api)
    manager = api[1]
    context = manager.get_project_context("demo")
    directory = tmp_path / "mapping_update"
    directory.mkdir()
    kegg = directory / "new.tsv"
    kegg.write_text("gene_id\tpathway_id\ng_up\tmap01200\n")
    assert FileImportService(manager).replace_file("demo", kegg, "kegg_mapping").status == "succeeded"
    assert run(api, "volcano", {"color_up": "red"}).status_code == 200
    original = json.loads((context.count_matrix.parent.parent / "import.json").read_bytes())["original_path"]
    with (context.project_root / original).open("ab") as out:
        out.write(b"\n")
    assert run(api, "volcano", {}).json()["error"]["code"] == "IMPORTED_FILE_CHANGED"


@pytest.mark.parametrize("missing", ["sha256", "design_formula"])
def test_unknown_provenance_fails_closed_and_old_run_still_readable(api, missing):
    deg = run(api).json()
    context = api[1].get_project_context("demo")
    path = (context.project_root / deg["outputs"]["deg_csv"]).parent.parent / "provenance.json"
    data = json.loads(path.read_bytes())
    if missing == "sha256":
        del data["upstream_sources"]["metadata_path"]["sha256"]
    else:
        del data["design_formula"]
    path.write_text(json.dumps(data), encoding="utf-8")
    response = run(api, "volcano", {})
    assert response.json()["error"]["code"] == "DEG_INPUT_VERSION_UNVERIFIED"
    assert api[0].get("/projects/demo/results/" + deg["run_id"]).status_code == 200


def test_direction_and_source_parameters(api):
    run(api)
    resolver = ResultResolver(api[1], "demo")
    assert resolver.find_compatible_result(skill_name="rnaseq.deg", treatment="control",
                                           control="treatment").status == "not_found"
    assert resolver.find_compatible_result(skill_name="rnaseq.deg",
        expected_deg_parameters={"alpha": 0.001}).error_code == "STALE_DEG_RESULT"


def test_three_group_source_requires_the_actual_pair_samples(api, tmp_path):
    manager = api[1]
    meta = manager.get_project_context("demo").metadata
    with meta.open(newline="") as stream:
        rows = list(csv.reader(stream))
    for group in ("treatment", "control"):
        next(row for row in rows if row[1] == group)[1] = "other"
    source = tmp_path / "three_groups.csv"
    with source.open("w", newline="") as stream:
        csv.writer(stream).writerows(rows)
    assert FileImportService(manager).replace_file("demo", source, "metadata").status == "succeeded"
    deg = run(api).json()
    assert run(api, "volcano", {}).status_code == 200
    directory = (manager.open_project("demo").root / deg["outputs"]["deg_csv"]).parent.parent
    record = directory / "provenance.json"
    data = json.loads(record.read_bytes())
    data["sample_selection"]["selected_samples"].pop()
    record.write_text(json.dumps(data), encoding="utf-8")
    assert run(api, "volcano", {}).json()["error"]["code"] == "DEG_INPUT_VERSION_UNVERIFIED"


def test_change_after_selection_prevents_dispatch(api, tmp_path, monkeypatch):
    run(api)
    previous = WorkflowExecutor.execute

    def change(self, plan, **kwargs):
        assert kwargs["current_deg"]
        replace(api[1], "demo", "metadata", tmp_path)
        return previous(self, plan, **kwargs)

    monkeypatch.setattr(WorkflowExecutor, "execute", change)
    response = run(api, "volcano", {})
    assert response.status_code == 409
    assert "STALE_DEG_RESULT" in response.text
    assert [n for n, _ in api[2]] == ["rnaseq.deg"]


def test_streamlit_chat_declines_stale_recent_deg(host, tmp_path):
    controller, answers, _, calls, _ = host
    assert run_workflow(host).status == "succeeded"
    before = calls.copy()
    replace(controller.manager, "First", "count_matrix", tmp_path)
    answers.append({"request_type": "result_reuse", "source_reference": "recent", "target_skill": "rnaseq.volcano"})
    reply = controller.submit("First", "把刚才的差异表达结果画成火山图", str(uuid4()))
    assert (reply.status, reply.error_code) == ("needs_input", "STALE_DEG_RESULT")
    assert calls == before
