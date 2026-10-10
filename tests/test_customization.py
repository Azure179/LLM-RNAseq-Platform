"""Controlled customization with synthetic data/mock API; no invented live results."""

import csv
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock
from types import SimpleNamespace

import httpx
import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.customization.catalog import validate_decision
from bulk_rnaseq.customization.engine import CustomizationEngine
from bulk_rnaseq.customization.filtering import read_annotations
from bulk_rnaseq.customization.models import CustomizationDecision
from bulk_rnaseq.customization.planner import CustomizationPlan, CustomizationPlanner
from bulk_rnaseq.llm.client import ChatResponse, DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.skills.volcano import run_volcano
from bulk_rnaseq.skills.heatmap import run_heatmap
from bulk_rnaseq.workspace import ProjectManager
from test_volcano import deg_file, fake_renderer
from test_heatmap import heatmap_inputs, renderer
from plot_helpers import fixture_png


def plan(mode, target, params):
    decision = CustomizationDecision(mode=mode, target_skill=target, parameters=params,
                                    reason="Unsupported test case" if mode == "unsupported" else "")
    return CustomizationPlan(decision, ChatResponse("fixture", "mock-only", "{}", "stop", None, 0.), "Synthetic test request")


@pytest.fixture
def project(tmp_path):
    manager = ProjectManager(tmp_path / "workspace")
    snapshot = manager.create_project("custom_test")
    source = snapshot.root / "results" / "source"
    source.mkdir()
    table = source / "deg.csv"
    table.write_text("gene_id,baseMean,log2FoldChange,pvalue,padj\nfoxl2,10,2,0.001,0.01\nsox9,20,-3,0.001,0.01\nlipid,30,4,0.001,0.01\n")
    annotation = snapshot.root / "input" / "annotations.tsv"
    annotation.write_text("#query\tDescription\tGOs\tKEGG_Pathway\nfoxl2\tgonadal development sex differentiation\tGO:0000001\tmap00001\nsox9\tsex differentiation\tGO:0000001\tmap00001\nlipid\tlipid metabolism\tGO:0000002\tmap00002\n")
    result = SkillResult("succeeded", "rnaseq.deg", "source-run",
        inputs={"parameters": {"alpha": .05, "lfc_threshold": 1}},
        outputs={"deg_csv": str(table), "filtered_csv": str(table)},
        metadata={"provenance": {"artifact_sha256": {table.name: sha256(table)}}})
    write_json(source / "summary.json", result.to_dict())
    return manager, snapshot, table, annotation


def engine(project):
    return CustomizationEngine(project[0], "custom_test", create_default_registry())


def test_volcano_parameters_union(deg_file, tmp_path, monkeypatch):
    mocked = Mock(side_effect=fake_renderer)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", mocked)
    source_hash = sha256(deg_file)
    result = run_volcano(deg_file, tmp_path / "custom", top_n_labels=1, label_genes=["down", "small", "not_found"],
                         color_up="red", color_down="blue", color_ns="#888888", figure_width=1800, title="Customized")
    args = mocked.call_args.args[0]
    assert args[args.index("--color-up") + 1] == "#FF0000"
    assert args[args.index("--width") + 1] == "1800"
    with result.plot_data_csv.open() as handle:
        labels = {r["label"] for r in csv.DictReader(handle) if r["label"]}
    assert labels == {"zero", "down", "small"}
    assert sha256(deg_file) == source_hash
    assert any(w["code"] == "LABEL_GENES_UNAVAILABLE" for w in result.to_skill_result().warnings)


def test_heatmap_options(heatmap_inputs, tmp_path, monkeypatch):
    matrix, deg, metadata = heatmap_inputs
    mocked = Mock(side_effect=renderer)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", mocked)
    result = run_heatmap(deg, metadata, "cohort", tmp_path / "heatmap", normalized_expression=matrix,
        gene_list=["g1", "g2"], ranking_method="abs_log2fc", top_n=1, show_gene_names=False, title="My heatmap", figure_height=1200)
    rows = list(csv.DictReader((result.output_dir / "selected_genes.csv").open()))
    assert rows[0]["gene_id"] == "g2"
    assert mocked.call_args.args[0][1].endswith("plot_heatmap.R")
    assert sha256(matrix) == sha256(result.output_dir / "normalized_expression.csv")


@pytest.mark.parametrize("subset,expected", [("up", ["foxl2"]), ("down", ["sox9"]), ("all", ["foxl2", "sox9"])])
def test_annotation_filter(project, subset, expected):
    before = sha256(project[2])
    request = plan("annotation_filter", "rnaseq.deg", {"gene_subset": subset, "keywords": ["sex differentiation"], "annotation_sources": ["gene_description"]})
    result = engine(project).execute(request, source_run_id="source-run", annotation_files=[project[3]])
    assert result["status"] == "succeeded", result
    with Path(result["outputs"]["filtered_table"]).open() as handle:
        rows = list(csv.DictReader(handle))
    assert [r["gene_id"] for r in rows] == expected and "baseMean" in rows[0]
    assert result["source_run_id"] == "source-run" and result["model"] == "mock-only"
    assert sha256(project[2]) == before
    assert Path(result["outputs"]["filtered_table"]).parent.parent.joinpath("customization.json").is_file()


def test_filter_sorting_empty_and_exact_keywords(project):
    app = engine(project)
    result = app.execute(plan("annotation_filter", "rnaseq.deg", {"order_by": "log2fc", "descending": True}), source_run_id="source-run")
    with Path(result["outputs"]["filtered_table"]).open() as handle:
        assert [r["gene_id"] for r in csv.DictReader(handle)] == ["lipid", "foxl2", "sox9"]
    empty = app.execute(plan("annotation_filter", "rnaseq.deg", {"keywords": ["not a real annotation"], "annotation_sources": ["GO"]}),
                        source_run_id="source-run", annotation_files=[project[3]])
    assert empty["status"] == "succeeded" and empty["skill_result"]["summary"]["n_matched_genes"] == 0


@pytest.mark.parametrize("params", [{"shell_command": "whoami"}, {"output_dir": "../escape"}, {"rscript_executable": "cmd"},
    {"figure_width": 1200.0}, {"label_genes": ["x", "x"]}, {"color_up": "not_a_color"}, {"cluster_rows": True}])
def test_unknown_and_dangerous_parameters(project, params):
    result = engine(project).execute(plan("parameter_override", "rnaseq.volcano", params), source_run_id="source-run")
    assert result["status"] == "failed" and not result["outputs"]


def test_path_escape_and_source_mismatch(project, tmp_path):
    foreign = tmp_path / "foreign.tsv"
    foreign.write_text("gene_id\tdescription\nx\tlipid\n")
    result = engine(project).execute(plan("annotation_filter", "rnaseq.deg", {}), source_run_id="source-run", annotation_files=[foreign])
    assert result["status"] == "failed"
    result = engine(project).execute(plan("parameter_override", "rnaseq.kegg", {"top_n": 15}), source_run_id="source-run")
    assert result["error_code"] == "CUSTOMIZATION_SOURCE_MISMATCH"


def test_unsupported_no_execution(project, monkeypatch):
    registry = create_default_registry()
    mocked = Mock(side_effect=AssertionError("Must not execute"))
    monkeypatch.setattr(registry, "execute", mocked)
    result = CustomizationEngine(project[0], "custom_test", registry).execute(plan("unsupported", "rnaseq.volcano", {}), source_run_id="source-run")
    assert result["status"] == "unsupported"
    audit = project[1].root / "results" / "customizations" / result["customization_id"] / "customization.json"
    assert json.loads(audit.read_text())["source_skill"] == "rnaseq.deg"
    mocked.assert_not_called()


def test_changed_source_rejected(project):
    project[2].write_text(project[2].read_text().replace("foxl2,10", "foxl2,999"))
    result = engine(project).execute(plan("annotation_filter", "rnaseq.deg", {}), source_run_id="source-run")
    assert result["error_code"] == "UPSTREAM_ARTIFACT_CHANGED"
    assert result["status"] == "failed" and not result["outputs"]


def test_customization_id_collision_never_overwrites(project, monkeypatch):
    app = engine(project)
    request = plan("annotation_filter", "rnaseq.deg", {})
    result = app.execute(request, source_run_id="source-run")
    audit = project[1].root / "results" / "customizations" / result["customization_id"] / "customization.json"
    original = audit.read_bytes()
    monkeypatch.setattr("bulk_rnaseq.customization.engine.uuid4", lambda: SimpleNamespace(hex=result["customization_id"]))
    repeated = app.execute(request, source_run_id="source-run")
    assert repeated["status"] == "failed" and audit.read_bytes() == original


def test_no_annotation_id_match(project):
    project[3].write_text("gene_id\tdescription\nunrelated\tsex differentiation\n")
    result = engine(project).execute(plan("annotation_filter", "rnaseq.deg", {
        "keywords": ["sex differentiation"], "annotation_sources": ["gene_description"]}),
        source_run_id="source-run", annotation_files=[project[3]])
    assert result["error_code"] == "ANNOTATION_ID_MISMATCH"
    assert "filtered_source.csv" in result["input_bindings"]


def test_all_keywords_across_annotations(project):
    result = engine(project).execute(plan("annotation_filter", "rnaseq.deg", {
        "keywords": ["gonadal", "GO:0000001"], "annotation_sources": ["gene_description", "GO"], "match_mode": "all"}),
        source_run_id="source-run", annotation_files=[project[3]])
    assert result["status"] == "succeeded"
    assert result["skill_result"]["summary"]["n_matched_genes"] == 1
    with Path(result["outputs"]["filtered_table"]).open() as handle:
        assert next(csv.DictReader(handle))["gene_id"] == "foxl2"


def test_engine_heatmap_reuses_vst(project, heatmap_inputs, monkeypatch):
    # Move synthetic inputs through the host-owned project input directory.
    local = []
    for path in heatmap_inputs:
        copied = project[1].root / "input" / path.name
        copied.write_bytes(path.read_bytes())
        local.append(copied)
    matrix, deg, metadata = local
    mocked = Mock(side_effect=renderer)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", mocked)
    source = run_heatmap(deg, metadata, "cohort", project[1].root / "results" / "heatmap_source", normalized_expression=matrix)
    source_result = source.to_skill_result()
    source_hash = sha256(source.output_dir / "normalized_expression.csv")
    app = engine(project)
    real_execute = app.registry.execute
    executed = Mock(side_effect=real_execute)
    monkeypatch.setattr(app.registry, "execute", executed)
    result = app.execute(plan("parameter_override", "rnaseq.heatmap", {"top_n": 1, "title": "New title"}), source_run_id=source_result.run_id)
    assert result["status"] == "succeeded", result
    assert result["skill_result"]["summary"]["n_genes_used"] == 1
    assert executed.call_count == 1 and mocked.call_count == 2  # Both calls plot only.
    assert sha256(source.output_dir / "normalized_expression.csv") == source_hash


def test_engine_volcano_uses_registry_and_source_thresholds(project, monkeypatch):
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fake_renderer)
    app = engine(project)
    executed = Mock(wraps=app.registry.execute)
    monkeypatch.setattr(app.registry, "execute", executed)
    result = app.execute(plan("parameter_override", "rnaseq.volcano", {"color_up": "red"}), source_run_id="source-run")
    assert result["status"] == "succeeded", result
    assert executed.call_args.args[0] == "rnaseq.volcano"
    assert result["skill_result"]["inputs"]["parameters"]["padj_threshold"] == .05


def fake_replot(command, **kwargs):
    assert Path(command[1]).name == "replot_enrichment.R"
    destination = Path(command[command.index("--output") + 1])
    for name in ("dotplot.png", "barplot.png"):
        (destination / name).write_bytes(fixture_png())
    return subprocess.CompletedProcess(command, 0, "R_VERSION=fixture\nN_DISPLAYED=1\n", "")


def enrichment_source(project, kind):
    root = project[1].root / "results" / kind
    root.mkdir()
    path = root / "enrichment.csv"
    path.write_text(f"{'go_id' if kind == 'go' else 'pathway_id'},description,padj,overlap_count,gene_ratio,fold_enrichment\n"
                    "id1,synthetic annotation,0.01,5,0.5,2\nid2,synthetic annotation,0.02,6,0.6,3\n")
    source = SkillResult("succeeded", "rnaseq." + kind, kind + "-run", outputs={"enrichment": str(path)},
                         metadata={"provenance": {"artifact_sha256": {path.name: sha256(path)}}})
    write_json(root / "summary.json", source.to_dict())
    return path


@pytest.mark.parametrize("kind", ["go", "kegg"])
def test_replot_only_no_statistics(project, monkeypatch, kind):
    source = enrichment_source(project, kind)
    before = sha256(source)
    mocked = Mock(side_effect=fake_replot)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", mocked)
    app = engine(project)
    monkeypatch.setattr(app.registry, "execute", Mock(side_effect=AssertionError("Replot must not rerun enrichment")))
    result = app.execute(plan("parameter_override", "rnaseq." + kind, {"top_n": 1, "order_by": "overlap_count"}), source_run_id=kind + "-run")
    assert result["status"] == "succeeded", result
    assert result["skill_result"]["summary"] == {"n_available": 2, "n_displayed": 1}
    assert sha256(source) == before and mocked.call_count == 1
    with Path(result["outputs"]["plot_data"]).open() as handle:
        assert next(csv.DictReader(handle))["description"] == "synthetic annotation"


@pytest.mark.parametrize("failure", ["timeout", "invalid_output", "exit_code", "wrong_count"])
def test_render_failure_preserves_source(project, monkeypatch, failure):
    source = enrichment_source(project, "kegg")
    before = sha256(source)
    def fail(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if failure == "exit_code":
            return subprocess.CompletedProcess(command, 2, "", "synthetic failure")
        if failure == "wrong_count":
            fake_replot(command)
            return subprocess.CompletedProcess(command, 0, "R_VERSION=fixture\nN_DISPLAYED=999\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fail)
    result = engine(project).execute(plan("parameter_override", "rnaseq.kegg", {"top_n": 15}), source_run_id="kegg-run")
    assert result["status"] == "failed" and result.get("error_code")
    assert sha256(source) == before


@pytest.mark.parametrize("body,valid", [
    ({"mode": "parameter_override", "target_skill": "rnaseq.volcano", "parameters": {"color_up": "red", "label_genes": ["foxl2"]}}, True),
    ({"mode": "parameter_override", "target_skill": "rnaseq.volcano", "parameters": {"label_genes": ["invented"]}}, False),
    ({"mode": "parameter_override", "target_skill": "rnaseq.volcano", "parameters": {"output_dir": "evil"}}, False),
    ({"mode": "unsupported", "target_skill": "rnaseq.volcano", "reason": "Arbitrary code is unsupported."}, True),
])
def test_mock_planner(body, valid):
    def respond(request):
        payload = json.loads(request.content)
        assert "tools" not in payload
        return httpx.Response(200, json={"id": "fixture", "model": "fixture", "choices": [{"message": {
            "role": "assistant", "content": json.dumps(body)}, "finish_reason": "stop"}]})
    client = DeepSeekClient(DeepSeekConfig("fixture-secret"), transport=httpx.MockTransport(respond))
    planner = CustomizationPlanner(client, create_default_registry())
    if valid:
        assert planner.plan("标注foxl2，上调红色", target_skill="rnaseq.volcano").decision.mode == body["mode"]
    else:
        with pytest.raises(DeepSeekError):
            planner.plan("标注foxl2，上调红色", target_skill="rnaseq.volcano")
