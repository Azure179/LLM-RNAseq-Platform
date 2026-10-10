"""Dispatch tests use explicitly synthetic SkillResult fixtures; no R or network."""

import csv
from dataclasses import replace
import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workflow import PlanValidator, ProjectHistory, WorkflowExecutor, WorkflowPlan, WorkflowResult
from bulk_rnaseq.workspace import ProjectManager
from bulk_rnaseq.workspace.file_models import FileRole


def deg():
    return {"step_id": "deg", "skill_name": "rnaseq.deg", "parameters": {"treatment": "treatment", "control": "control"}}


def reference(output="deg_csv", **extra):
    return {"source": "workflow_step", "step_id": "deg", "output": output, **extra}


def volcano():
    return {"step_id": "a_volcano", "skill_name": "rnaseq.volcano", "input_bindings": {"deg_result_file": reference()}}


def kegg(subset="up"):
    return {"step_id": "kegg", "skill_name": "rnaseq.kegg", "input_bindings": {
        "gene_list": reference("filtered_csv", gene_subset=subset), "analysis_genes": reference()}}


def pca():
    return {"step_id": "pca", "skill_name": "rnaseq.pca", "parameters": {"group_column": "condition"}}


@pytest.fixture
def host(tmp_path, demo_files, request):
    # Keep opt-in real execution artifacts for inspection; default unit tests use tmp_path.
    root = (Path(request.config.rootpath) / "outputs" / "workflow_tests" / uuid4().hex
            if request.node.get_closest_marker("integration") else tmp_path)
    manager = ProjectManager(root / "workspace")
    project = manager.create_project("demo")
    for role, source in zip((FileRole.COUNT_MATRIX, FileRole.METADATA), demo_files):
        target = project.root / "input" / source.name
        shutil.copyfile(source, target)
        manager.commit_file_binding("demo", role, target.relative_to(project.root).as_posix(),
            expected_updated_at=manager.open_project("demo").metadata.updated_at)
    mapping = project.root / "input" / "mapping.tsv"
    # Synthetic demo annotations for testing plumbing, not biological claims.
    mapping.write_text("gene_id\tpathway_id\n" + "".join(f"gene_{i:03d}\tmap01100\n" for i in range(1, 31)), encoding="utf-8")
    manager.commit_file_binding("demo", FileRole.KEGG_MAPPING, "input/mapping.tsv",
        expected_updated_at=manager.open_project("demo").metadata.updated_at)
    return manager, create_default_registry()


def validate(host, *steps):
    manager, registry = host
    plan = WorkflowPlan(project_id="demo", user_request="Executor test fixture, no model invocation", steps=list(steps))
    checked = PlanValidator(registry).validate_project(plan, manager.get_project_context("demo"), history=ProjectHistory(manager, "demo"))
    assert checked.valid, checked.errors
    return checked


def executor(host, **kwargs):
    return WorkflowExecutor(host[1], host[0], "demo", **kwargs)


@pytest.fixture
def fake_dispatch(host, monkeypatch):
    calls, failures = [], set()

    def execute(name, inputs):
        calls.append((name, inputs))
        if name in failures:
            return SkillResult("failed", name, uuid4().hex, error_code="TEST_FAILURE", message="Synthetic execution failure")
        root = Path(inputs["output_dir"])
        root.mkdir()
        outputs = {}
        if name == "rnaseq.deg":
            for key, filename in (("deg_csv", "deg_results.csv"), ("filtered_csv", "deg_filtered.csv")):
                target = root / filename
                target.write_text("gene_id,log2FoldChange,pvalue,padj\ng_up,2,0.001,0.01\ng_down,-2,0.001,0.01\n", encoding="utf-8")
                outputs[key] = str(target)
        else:
            target = root / "fixture.txt"
            target.write_text("Synthetic unit-test artifact only", encoding="utf-8")
            outputs["plot"] = str(target)
        result = SkillResult("succeeded", name, uuid4().hex, outputs=outputs,
            metadata={"provenance": {"artifact_sha256": {Path(p).name: sha256(Path(p)) for p in outputs.values()}}})
        write_json(root / "summary.json", result.to_dict())
        return result

    monkeypatch.setattr(host[1], "execute", execute)
    return calls, failures


@pytest.mark.parametrize("steps", [[deg(), volcano()], [deg(), kegg()], [volcano(), kegg(), deg()], [pca(), deg()]])
def test_success_dags(host, fake_dispatch, steps):
    checked = validate(host, *steps)
    result = executor(host).execute(checked)
    assert result.status == "succeeded"
    assert result.completed_steps == checked.execution_order
    assert len(fake_dispatch[0]) == len(steps)
    assert WorkflowResult.model_validate_json(result.model_dump_json()) == result
    root = host[0].get_project_context("demo").output_root / "workflows" / result.workflow_id
    assert json.loads((root / "workflow_result.json").read_text(encoding="utf-8"))["status"] == "succeeded"
    for record in result.step_records.values():
        assert record["run_id"] and record["output_sha256"]


def test_multigroup_project_selects_only_deg_pair_and_records_provenance(host, fake_dispatch):
    project = host[0].get_project_context("demo")
    with project.count_matrix.open(newline="") as handle:
        rows = list(csv.reader(handle))
    with project.count_matrix.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([*rows[0], "other_1", "other_2"])
        writer.writerows([*row, "10", "12"] for row in rows[1:] if row)
    with project.metadata.open("a") as handle:
        handle.write("\nother_1,other\nother_2,other\n")
    original = sha256(project.count_matrix), sha256(project.metadata)
    result = executor(host).execute(validate(host, deg(), pca()))
    assert result.status == "succeeded", result.step_results
    inputs = dict(fake_dispatch[0])
    assert Path(inputs["rnaseq.pca"]["count_matrix"]) == project.count_matrix
    record = result.step_records["deg"]
    assert record["sample_selection"]["excluded_samples"] == ["other_1", "other_2"]
    for key in ("count_matrix_path", "metadata_path"):
        provenance = record["upstream_sources"][key]
        assert provenance["derived_sha256"] == sha256(Path(provenance["derived_path"]))
        assert Path(inputs["rnaseq.deg"][key]) == Path(provenance["derived_path"])
    assert original == (sha256(project.count_matrix), sha256(project.metadata))


@pytest.mark.parametrize("subset,expected", [("up", ["g_up"]), ("down", ["g_down"]), ("all", ["g_up", "g_down"])])
def test_subsets_exact_and_background_unfiltered(host, fake_dispatch, subset, expected):
    result = executor(host).execute(validate(host, deg(), kegg(subset)))
    assert result.status == "succeeded"
    inputs = fake_dispatch[0][-1][1]
    with Path(inputs["gene_list"]).open() as handle:
        assert [r["gene_id"] for r in csv.DictReader(handle)] == expected
    assert Path(inputs["analysis_genes"]).name == "deg_results.csv"
    provenance = result.step_records["kegg"]["upstream_sources"]["gene_list"]
    assert provenance["run_id"] == result.step_results["deg"]["run_id"]
    assert provenance["selected_genes"] == len(expected)


def test_failed_parent_skips_children_not_independent(host, fake_dispatch):
    fake_dispatch[1].add("rnaseq.deg")
    result = executor(host).execute(validate(host, deg(), volcano(), kegg(), pca()))
    assert result.failed_steps == ["deg"]
    assert result.skipped_steps == ["a_volcano", "kegg"]
    assert result.completed_steps == ["pca"]
    assert result.status == "partial"
    assert [name for name, _ in fake_dispatch[0]] == ["rnaseq.deg", "rnaseq.pca"]


def test_failed_sibling_does_not_block(host, fake_dispatch):
    fake_dispatch[1].add("rnaseq.volcano")
    result = executor(host).execute(validate(host, deg(), volcano(), kegg()))
    assert result.failed_steps == ["a_volcano"]
    assert result.completed_steps == ["deg", "kegg"]
    assert result.status == "partial"


def test_history_reuses_exact_run_no_deg(host, fake_dispatch):
    first = executor(host).execute(validate(host, deg()))
    step = volcano()
    step["input_bindings"]["deg_result_file"] = {"source": "project_result", "run_id": first.step_results["deg"]["run_id"], "output": "deg_csv"}
    fake_dispatch[0].clear()
    second = executor(host).execute(validate(host, step))
    assert second.status == "succeeded"
    assert [name for name, _ in fake_dispatch[0]] == ["rnaseq.volcano"]


def test_altered_historical_artifact_refused(host, fake_dispatch):
    first = executor(host).execute(validate(host, deg()))
    run = first.step_results["deg"]
    step = volcano()
    step["input_bindings"]["deg_result_file"] = {"source": "project_result", "run_id": run["run_id"], "output": "deg_csv"}
    checked = validate(host, step)
    Path(run["outputs"]["deg_csv"]).write_text("changed", encoding="utf-8")
    fake_dispatch[0].clear()
    result = executor(host).execute(checked)
    assert result.step_results["a_volcano"]["error_code"] == "UPSTREAM_ARTIFACT_CHANGED"
    assert not fake_dispatch[0]


def test_reject_unvalidated_and_tampered_order(host, fake_dispatch):
    with pytest.raises(SkillError, match="PlanValidationResult"):
        executor(host).execute(WorkflowPlan(project_id="demo", user_request="x", steps=[deg()]))
    checked = validate(host, deg(), volcano())
    checked.execution_order.reverse()
    with pytest.raises(SkillError) as error:
        executor(host).execute(checked)
    assert error.value.code == "WORKFLOW_VALIDATION_CHANGED"
    assert not fake_dispatch[0]


def test_duplicate_execution_refused(host, fake_dispatch):
    checked = validate(host, deg())
    executor(host).execute(checked)
    with pytest.raises(SkillError) as error:
        executor(host).execute(checked)
    assert error.value.code == "WORKFLOW_ALREADY_EXISTS"
    assert len(fake_dispatch[0]) == 1


def test_stale_project_input_refused(host, fake_dispatch):
    checked = validate(host, deg())
    host[0].get_project_context("demo").metadata.unlink()
    with pytest.raises(SkillError):
        executor(host).execute(checked)
    assert not fake_dispatch[0]


def test_empty_subset_is_not_fake_enrichment(host, fake_dispatch, monkeypatch):
    original = host[1].execute
    def dispatch(name, inputs):
        result = original(name, inputs)
        if name == "rnaseq.deg":
            Path(result.outputs["filtered_csv"]).write_text("gene_id,log2FoldChange,pvalue,padj\n", encoding="utf-8")
            result = replace(result, metadata={"provenance": {"artifact_sha256": {
                Path(p).name: sha256(Path(p)) for p in result.outputs.values()}}})
        return result
    monkeypatch.setattr(host[1], "execute", dispatch)
    result = executor(host).execute(validate(host, deg(), kegg(), volcano()))
    assert result.step_results["kegg"]["error_code"] == "EMPTY_GENE_SUBSET"
    assert result.completed_steps == ["deg", "a_volcano"]
    assert all(name != "rnaseq.kegg" for name, _ in fake_dispatch[0])


def test_unexpected_executor_exception_keeps_other_root(host, fake_dispatch, monkeypatch):
    original = host[1].execute
    def dispatch(name, inputs):
        if name == "rnaseq.deg":
            raise RuntimeError("Synthetic bug")
        return original(name, inputs)
    monkeypatch.setattr(host[1], "execute", dispatch)
    result = executor(host).execute(validate(host, deg(), pca()))
    assert result.status == "partial"
    assert result.completed_steps == ["pca"]
    assert result.step_results["deg"]["error_code"] == "WORKFLOW_STEP_FAILED"


@pytest.mark.parametrize("mutation", ["escape", "missing", "checksum"])
def test_bad_skill_artifact_never_consumed(host, fake_dispatch, monkeypatch, mutation, tmp_path):
    original = host[1].execute
    def dispatch(name, inputs):
        result = original(name, inputs)
        if name == "rnaseq.deg":
            if mutation == "escape":
                outside = tmp_path / "outside.csv"
                outside.write_text("not an authorized output")
                result = replace(result, outputs={**result.outputs, "deg_csv": str(outside)})
            elif mutation == "missing":
                Path(result.outputs["deg_csv"]).unlink()
            else:
                Path(result.outputs["deg_csv"]).write_text("changed")
        return result
    monkeypatch.setattr(host[1], "execute", dispatch)
    result = executor(host).execute(validate(host, deg(), volcano()))
    assert result.status == "failed"
    assert result.failed_steps == ["deg"]
    assert result.skipped_steps == ["a_volcano"]
    assert len(fake_dispatch[0]) == 1


def test_input_changed_while_running_detected(host, fake_dispatch, monkeypatch):
    original = host[1].execute
    def dispatch(name, inputs):
        result = original(name, inputs)
        if name == "rnaseq.deg":
            host[0].get_project_context("demo").metadata.write_text("changed")
        return result
    monkeypatch.setattr(host[1], "execute", dispatch)
    result = executor(host).execute(validate(host, deg(), volcano(), pca()))
    assert result.step_results["deg"]["error_code"] == "WORKFLOW_INPUT_CHANGED"
    assert result.skipped_steps == ["a_volcano"]
    assert result.step_results["pca"]["error_code"] == "PROJECT_INPUT_CHANGED"


def test_history_project_isolation_and_exact_id(host, fake_dispatch):
    first = executor(host).execute(validate(host, deg()))
    run_id = first.step_results["deg"]["run_id"]
    history = ProjectHistory(host[0], "demo")
    assert history.lookup("another_project", run_id) is None
    assert history.lookup("demo", "unknown") is None
    assert history.lookup("demo", run_id).skill_name == "rnaseq.deg"


def test_reserved_id_rejected_before_write(host, fake_dispatch):
    checked = validate(host, deg())
    checked.validated_plan.workflow_id = "CON"
    with pytest.raises(SkillError) as error:
        executor(host).execute(checked)
    assert error.value.code == "INVALID_WORKFLOW_ID"
    assert not fake_dispatch[0]


def test_output_alias_preserved(host, fake_dispatch):
    step = deg()
    step["output_alias"] = "comparison"
    result = executor(host).execute(validate(host, step))
    assert result.outputs["comparison.deg_csv"].step_id == "deg"


def test_transitive_skip(host, fake_dispatch):
    first = {"step_id": "h1", "skill_name": "rnaseq.heatmap", "parameters": {"group_column": "condition"},
             "input_bindings": {"deg_result_file": reference(), "raw_counts": {"source": "project_input", "key": "count_matrix"}}}
    second = {"step_id": "h2", "skill_name": "rnaseq.heatmap", "parameters": {"group_column": "condition"},
              "input_bindings": {"deg_result_file": reference(), "normalized_expression": {
                  "source": "workflow_step", "step_id": "h1", "output": "normalized_expression"}}}
    fake_dispatch[1].add("rnaseq.heatmap")
    result = executor(host).execute(validate(host, deg(), first, second, volcano()))
    assert result.failed_steps == ["h1"]
    assert result.skipped_steps == ["h2"]
    assert result.completed_steps == ["deg", "a_volcano"]
    assert result.step_records["h2"]["blocked_by"] == ["h1"]


def test_warnings_retained(host, fake_dispatch, monkeypatch):
    original = host[1].execute
    def dispatch(name, inputs):
        return replace(original(name, inputs), warnings=[{"code": "TEST_WARNING", "message": "Synthetic warning"}])
    monkeypatch.setattr(host[1], "execute", dispatch)
    result = executor(host).execute(validate(host, deg()))
    assert "deg: TEST_WARNING: Synthetic warning" in result.warnings
    assert result.step_results["deg"]["warnings"][0]["code"] == "TEST_WARNING"


@pytest.mark.integration
@pytest.mark.parametrize("case", ["deg_volcano", "deg_kegg_up", "branch_and_roots"])
def test_real_workflows(host, request, case):
    """Real Registry, R, DESeq2 and figures. Mapping associations are synthetic."""
    steps = {"deg_volcano": [deg(), volcano()], "deg_kegg_up": [deg(), kegg()],
             "branch_and_roots": [deg(), volcano(), kegg(), pca()]}[case]
    result = executor(host, rscript_executable=request.config.getoption("--rscript")).execute(validate(host, *steps))
    assert result.status == "succeeded", result.model_dump()
    write_json(host[0].root.parent / "integration_report.json", {
        "case": case, "status": result.status, "workflow_result": result.model_dump(mode="json"),
        "dataset": "Existing demo counts: 30 genes, 6 samples; synthetic KEGG associations", "api_requests": 0})
    for skill_result in result.step_results.values():
        assert skill_result["run_id"]
        assert all(Path(p).is_file() for p in skill_result["outputs"].values())
    if "kegg" in result.completed_steps:
        with Path(result.step_results["deg"]["outputs"]["filtered_csv"]).open() as handle:
            expected = {r["gene_id"] for r in csv.DictReader(handle) if float(r["log2FoldChange"]) > 0}
        with Path(result.step_records["kegg"]["inputs"]["gene_list"]).open() as handle:
            assert {r["gene_id"] for r in csv.DictReader(handle)} == expected
        with Path(result.step_results["kegg"]["outputs"]["query"]).open() as handle:
            assert {r["gene_id"] for r in csv.DictReader(handle)} == expected
    if case == "deg_volcano":
        first_deg = result.step_results["deg"]
        checksum = sha256(Path(first_deg["outputs"]["deg_csv"]))
        step = volcano()
        step["input_bindings"]["deg_result_file"] = {"source": "project_result", "run_id": first_deg["run_id"], "output": "deg_csv"}
        second = executor(host, rscript_executable=request.config.getoption("--rscript")).execute(validate(host, step))
        assert second.status == "succeeded", second.model_dump()
        assert second.completed_steps == ["a_volcano"]
        assert sha256(Path(first_deg["outputs"]["deg_csv"])) == checksum
