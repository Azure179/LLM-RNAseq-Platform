"""Trusted R rendering only; synthetic fixtures are not biological conclusions."""

import csv
from pathlib import Path
from unittest.mock import Mock
import subprocess
import pytest
from PIL import Image

from bulk_rnaseq.skills.volcano import run_volcano
from bulk_rnaseq.skills.heatmap import run_heatmap
from bulk_rnaseq.run_io import sha256
from bulk_rnaseq.customization.enrichment_plot import replot_enrichment
from bulk_rnaseq.customization.models import EnrichmentPlotOptions
from bulk_rnaseq.customization.engine import CustomizationEngine
from bulk_rnaseq.builtin_registry import create_default_registry
from test_customization import project, enrichment_source, plan
from test_volcano import deg_file
from test_heatmap import heatmap_inputs


@pytest.mark.integration
def test_real_customized_volcano(deg_file, tmp_path, request, monkeypatch):
    original_run = subprocess.run
    def rendering_only(command, **kwargs):
        assert Path(command[1]).name == "plot_volcano.R"
        return original_run(command, **kwargs)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", rendering_only)
    before = sha256(deg_file)
    result = run_volcano(deg_file, tmp_path / "out", label_genes=["down", "small"], color_up="red", color_down="blue",
        top_n_labels=1, figure_width=1600, figure_height=1000, title="Custom volcano", rscript_executable=request.config.getoption("--rscript"))
    with Image.open(result.plot_png) as image:
        assert image.size == (1600, 1000)
    assert result.to_skill_result().summary["n_labels"] == 3 and sha256(deg_file) == before


@pytest.mark.integration
@pytest.mark.parametrize("dimensions", [(1600, 1100), (600, 600)])
def test_real_customized_heatmap(heatmap_inputs, tmp_path, request, monkeypatch, dimensions):
    matrix, deg, metadata = heatmap_inputs
    original_run = subprocess.run
    def rendering_only(command, **kwargs):
        assert Path(command[1]).name == "plot_heatmap.R"  # Never normalize again.
        return original_run(command, **kwargs)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", rendering_only)
    result = run_heatmap(deg, metadata, "cohort", tmp_path / "out", normalized_expression=matrix, gene_list=["g1", "g2"],
        ranking_method="abs_log2fc", show_gene_names=False, title="Custom heatmap", figure_width=dimensions[0], figure_height=dimensions[1],
        rscript_executable=request.config.getoption("--rscript"))
    with Image.open(result.output_dir / "heatmap.png") as image:
        assert image.size == dimensions
    assert next(csv.DictReader((result.output_dir / "selected_genes.csv").open()))["gene_id"] == "g2"


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["go", "kegg"])
@pytest.mark.parametrize("dimensions", [(1400, 1000), (600, 600)])
def test_real_replot(project, request, kind, monkeypatch, dimensions):
    source = enrichment_source(project, kind)
    before = sha256(source)
    registry = create_default_registry()
    monkeypatch.setattr(registry, "execute", Mock(side_effect=AssertionError("No enrichment rerun")))
    original_run = subprocess.run
    def rendering_only(command, **kwargs):
        assert Path(command[1]).name == "replot_enrichment.R"
        return original_run(command, **kwargs)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", rendering_only)
    engine = CustomizationEngine(project[0], "custom_test", registry, rscript_executable=request.config.getoption("--rscript"))
    result = engine.execute(plan("parameter_override", "rnaseq." + kind, {"top_n": 1, "show_count": False,
        "figure_width": dimensions[0], "figure_height": dimensions[1]}), source_run_id=kind + "-run")
    assert result["status"] == "succeeded", result
    for key in ("dotplot", "barplot"):
        with Image.open(result["outputs"][key]) as image:
            image.verify()
    assert sha256(source) == before


@pytest.mark.integration
def test_real_empty_enrichment_replot(tmp_path, request):
    source = tmp_path / "empty.csv"
    source.write_text("pathway_id,description,padj,overlap_count,gene_ratio,fold_enrichment\n")
    output = tmp_path / "plots"
    output.mkdir()
    result = replot_enrichment(source, output, "kegg", EnrichmentPlotOptions(),
                              rscript_executable=request.config.getoption("--rscript"))
    assert result["n_available"] == result["n_displayed"] == 0
    for name in ("dotplot.png", "barplot.png"):
        with Image.open(output / name) as image:
            image.verify()
