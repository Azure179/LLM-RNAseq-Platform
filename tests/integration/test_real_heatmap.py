"""Real VST/heatmap rendering; DEG rows here are explicitly synthetic test selectors."""

import csv
import json
from pathlib import Path

import pytest

from bulk_rnaseq.heatmap_data import read_normalized_expression
from bulk_rnaseq.skills.heatmap import run_heatmap
from bulk_rnaseq.skills.pca import run_pca


@pytest.mark.integration
def test_raw_vst_reuse_and_pca_consistency(demo_files: tuple[Path, Path], tmp_path: Path,
                                          request: pytest.FixtureRequest) -> None:
    counts, metadata = demo_files
    deg = tmp_path / "synthetic_selector.csv"
    deg.write_text("gene_id,log2FoldChange,pvalue,padj\ngene_001,2,0.001,0.01\ngene_004,-2,0,0\n")
    rscript = request.config.getoption("--rscript")
    raw = run_heatmap(deg, metadata, "condition", tmp_path / "raw", raw_counts=counts, rscript_executable=rscript)
    normalized = raw.output_dir / "normalized_expression.csv"
    second = run_heatmap(deg, metadata, "condition", tmp_path / "reuse", normalized_expression=normalized,
                         rscript_executable=rscript)
    assert (raw.output_dir / "heatmap_matrix.csv").read_bytes() == (second.output_dir / "heatmap_matrix.csv").read_bytes()
    raw_summary = json.loads(raw.summary_json.read_text())
    summary = json.loads(second.summary_json.read_text())
    assert len(raw_summary["processes"]) == 2 and len(summary["processes"]) == 1
    assert summary["processes"][0]["stage"] == "plot_heatmap.R"
    assert raw_summary["metrics"]["n_genes_used"] == 2
    assert "deseq2_version" in raw_summary["provenance"]
    for result in (raw, second):
        for name in result.to_dict()["artifacts"].values():
            assert (result.output_dir / name).is_file()
        assert (result.output_dir / "heatmap.png").stat().st_size > 1000

    pca = run_pca(counts, metadata, "condition", tmp_path / "pca", rscript_executable=rscript)
    assert json.loads(pca.summary_json.read_text())["provenance"]["vst_module_sha256"] == raw_summary["provenance"]["r_scripts_sha256"]["vst_common.R"]
    # Independently verify PCA variances/eigenvectors against the exported shared VST matrix.
    matrix = read_normalized_expression(normalized)
    centered = [[v - sum(row) / len(row) for v in row] for row in matrix.values.values()]
    total_ss = sum(v * v for row in centered for v in row)
    with pca.coordinates_csv.open(newline="") as handle:
        coords = list(csv.DictReader(handle))
    variance = json.loads(pca.variance_json.read_text())
    assert [r["sample"] for r in coords] == list(matrix.samples)
    for pc in ("PC1", "PC2"):
        scores = [float(row[pc]) for row in coords]
        energy = sum(v * v for v in scores)
        assert 100 * energy / total_ss == pytest.approx(variance[pc], rel=1e-7)
        for i in range(len(scores)):
            gram_score = sum(row[i] * sum(v * s for v, s in zip(row, scores)) for row in centered)
            assert gram_score == pytest.approx(energy * scores[i], rel=1e-6, abs=1e-7)


@pytest.mark.integration
def test_single_gene_two_samples(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    expression, deg, metadata = (tmp_path / name for name in ("synthetic_vst.csv", "synthetic_deg.csv", "metadata.csv"))
    expression.write_text("gene_id,001,002\n001,-0.5,1.5\n")
    deg.write_text("gene_id,log2FoldChange,pvalue,padj\n001,2,0,0\n")
    metadata.write_text("sample_id,group\n002,B\n001,A\n")
    result = run_heatmap(deg, metadata, "group", tmp_path / "out", normalized_expression=expression,
                         top_n=1, rscript_executable=request.config.getoption("--rscript"))
    summary = json.loads(result.summary_json.read_text())
    assert summary["metrics"]["n_genes_used"] == 1 and summary["metrics"]["n_samples"] == 2
    assert (result.output_dir / "heatmap.png").stat().st_size > 1000
