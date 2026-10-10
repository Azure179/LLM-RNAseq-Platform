"""Real R/DESeq2 results: compare repeatability and contrast direction, not exact p-values."""

import csv
import json
from pathlib import Path

import pytest

from bulk_rnaseq.skills.deg import run_deg


@pytest.mark.integration
def test_real_demo_and_contrast(demo_files: tuple[Path, Path], tmp_path: Path,
                                request: pytest.FixtureRequest) -> None:
    rscript = request.config.getoption("--rscript")
    counts, metadata = demo_files
    forward = run_deg(counts, metadata, "treatment", "control", tmp_path / "forward",
                      rscript_executable=rscript)
    summary = json.loads(forward.summary_json.read_text())
    assert summary["process"]["return_code"] == 0
    assert summary["metrics"]["genes_analyzed"] == 30
    assert summary["provenance"]["deseq2_version"]
    with forward.deg_csv.open(newline="") as handle:
        results = {row["gene_id"]: float(row["log2FoldChange"]) for row in csv.DictReader(handle)}
    assert results["gene_001"] > 0
    assert results["gene_002"] < 0
    # Reordering metadata must not change sample assignment or results.
    lines = metadata.read_text().splitlines()
    metadata.write_text("\n".join([lines[0], *reversed(lines[1:])]) + "\n")
    reordered = run_deg(counts, metadata, "treatment", "control", tmp_path / "reordered",
                        rscript_executable=rscript)
    with reordered.deg_csv.open(newline="") as handle:
        for row in csv.DictReader(handle):
            assert float(row["log2FoldChange"]) == pytest.approx(results[row["gene_id"]], abs=1e-6)
    reverse = run_deg(counts, metadata, "control", "treatment", tmp_path / "reverse",
                      rscript_executable=rscript)
    with reverse.deg_csv.open(newline="") as handle:
        for row in csv.DictReader(handle):
            assert float(row["log2FoldChange"]) == pytest.approx(-results[row["gene_id"]], abs=1e-4)
