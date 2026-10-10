"""Real VST/PCA integration, with annotations independent of numerical computation."""

import csv
import json
import math
from pathlib import Path

import pytest

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.skills.pca import run_pca


@pytest.mark.integration
def test_real_pca_outputs_and_label_invariance(demo_files: tuple[Path, Path], tmp_path: Path,
                                               request: pytest.FixtureRequest) -> None:
    counts, metadata = demo_files
    rscript = request.config.getoption("--rscript")
    samples = ["control_1", "control_2", "control_3", "treatment_1", "treatment_2", "treatment_3"]
    # Three annotation groups, including labels that must remain strings.
    groups = ["001", "001", "002", "002", "003", "003"]
    with metadata.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", "cohort"])
        writer.writerows(zip(samples, groups))
    result = run_pca(counts, metadata, "cohort", tmp_path / "original", rscript_executable=rscript)
    summary = json.loads(result.summary_json.read_text())
    assert summary["status"] == "succeeded" and summary["operation"] == "pca"
    assert summary["metrics"]["n_samples"] == 6 and summary["metrics"]["n_groups"] == 3
    assert summary["metrics"]["n_genes_vst"] == 30
    for name in summary["artifacts"].values():
        assert (result.output_dir / name).is_file()
    with result.coordinates_csv.open(encoding="utf-8", newline="") as handle:
        coordinates = list(csv.DictReader(handle))
    assert [row["group"] for row in coordinates] == groups
    assert [row["sample"] for row in coordinates] == samples
    original = {row["sample"]: (float(row["PC1"]), float(row["PC2"])) for row in coordinates}
    variance = json.loads(result.variance_json.read_text())
    assert 0 < variance["PC2"] <= variance["PC1"] <= 100
    assert variance["PC1"] + variance["PC2"] <= 100 + 1e-7

    # Change metadata order and labels: blind VST must not use group assignments.
    with metadata.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", "cohort"])
        writer.writerows((sample, "one_group") for sample in reversed(samples))
    second = run_pca(counts, metadata, "cohort", tmp_path / "relabeled", rscript_executable=rscript)
    assert json.loads(second.variance_json.read_text()) == pytest.approx(variance, rel=1e-7)
    with second.coordinates_csv.open(encoding="utf-8", newline="") as handle:
        other = {row["sample"]: (float(row["PC1"]), float(row["PC2"])) for row in csv.DictReader(handle)}
    # PCA axis signs are arbitrary; pairwise distances are the relevant invariant.
    for i, first in enumerate(samples):
        for last in samples[i + 1:]:
            assert math.dist(original[first], original[last]) == pytest.approx(
                math.dist(other[first], other[last]), rel=1e-6, abs=1e-8)


@pytest.mark.integration
def test_real_pca_identical_samples_fail(demo_files: tuple[Path, Path], tmp_path: Path,
                                        request: pytest.FixtureRequest) -> None:
    counts, metadata = demo_files
    with counts.open(newline="") as handle:
        rows = list(csv.reader(handle))
    with counts.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(rows[0])
        for row in rows[1:]:
            if row:
                writer.writerow([row[0], *([row[1]] * 6)])
    output = tmp_path / "no_variation"
    with pytest.raises(SkillError) as caught:
        run_pca(counts, metadata, "condition", output,
                rscript_executable=request.config.getoption("--rscript"))
    assert caught.value.code == "RSCRIPT_FAILED" and caught.value.operation == "pca"
    assert not (output / "summary.json").exists()
    assert (output / "failure.json").is_file()
