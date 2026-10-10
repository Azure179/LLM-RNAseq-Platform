"""Read-only numerical/file cross-checks of the real run; no analysis is rerun."""

import argparse
from collections import Counter
import csv
import logging
import math
from pathlib import Path
from statistics import mean, stdev

from run_real_data_test import ROOT, read_json
from bulk_rnaseq.run_io import sha256
from prepare_inputs import local_path


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def verify(output: Path) -> dict[str, object]:
    report = read_json(output / "real_data_test_summary.json")
    for name, digest in report["provenance"]["source_sha256"].items():
        assert sha256(local_path(ROOT, name)) == digest, f"Source changed: {name}"
    for name, digest in report["provenance"]["prepared_sha256"].items():
        assert sha256(local_path(ROOT, name)) == digest, f"Prepared input changed: {name}"
    summaries = {}
    for name, step in report["steps"].items():
        if "summary_json" not in step:
            continue
        path = Path(step["summary_json"])
        payload = summaries[name] = read_json(path)
        for artifact, digest in payload["provenance"]["artifact_sha256"].items():
            assert sha256(path.parent / artifact) == digest, f"Artifact changed: {artifact}"
    deg = rows(output / "02_deg/deg_results.csv")
    filtered = rows(output / "02_deg/deg_filtered.csv")
    threshold = report["resolved_config"]["deg"]
    significant = [row for row in deg if row["padj"] not in {"NA", ""} and row["log2FoldChange"] not in {"NA", ""}
                   and float(row["padj"]) < threshold["padj_threshold"] and abs(float(row["log2FoldChange"])) >= threshold["log2fc_threshold"]]
    assert {r["gene_id"] for r in filtered} == {r["gene_id"] for r in significant}
    plotted = rows(output / "03_volcano/volcano_plot_data.csv")
    assert {r["gene_id"] for r in plotted} == {r["gene_id"] for r in deg}
    categories = Counter(row["category"] for row in plotted)
    assert categories["Up"] == report["key_results"]["n_up"]
    assert categories["Down"] == report["key_results"]["n_down"]
    assert categories["NS"] == report["key_results"]["n_ns"]
    assert all(math.isfinite(float(row["neg_log10_padj"])) for row in plotted if row["plotted"] == "true")
    samples = report["data_check"]["sample_names"]
    normalized = {row["gene_id"]: row for row in rows(output / "04_heatmap/normalized_expression.csv")}
    matrix = rows(output / "04_heatmap/heatmap_matrix.csv")
    assert len(matrix) == summaries["heatmap"]["metrics"]["n_genes_used"]
    max_error = 0.0
    for row in matrix:
        values = [float(normalized[row["gene_id"]][sample]) for sample in samples]
        expected = [(value - mean(values)) / stdev(values) for value in values]
        max_error = max(max_error, *(abs(float(row[sample]) - z) for sample, z in zip(samples, expected)))
    assert max_error < 1e-9, "Heatmap values differ from row-zscored VST"
    significant.sort(key=lambda row: (float(row["padj"]), -abs(float(row["log2FoldChange"])), row["gene_id"]))
    assert [row["gene_id"] for row in matrix] == [row["gene_id"] for row in significant[:len(matrix)]]
    coordinates = rows(output / "01_pca/pca_coordinates.csv")
    assert {row["sample"] for row in coordinates} == set(samples)
    assert summaries["pca"]["provenance"]["input_sha256"]["count_matrix"] == summaries["heatmap"]["provenance"]["input_sha256"]["raw_counts"]
    assert summaries["pca"]["provenance"]["vst_module_sha256"] == summaries["heatmap"]["provenance"]["r_scripts_sha256"]["vst_common.R"]
    for name, folder in (("go", "05_go"), ("kegg", "06_kegg")):
        step = report["steps"][name]
        if step["status"] == "failed":
            failure = read_json(output / folder / "failure.json")
            assert failure["error"]["code"] == step["error"]["error"]["code"]
            assert not (output / folder / "summary.json").exists()
            assert report["key_results"]["n_go_terms" if name == "go" else "n_kegg_pathways"] is None
    return {"artifact_hashes": "passed", "source_preservation": "passed", "deg_filter_and_volcano": "passed",
            "heatmap_gene_order_and_vst_zscore": "passed", "max_zscore_error": max_error,
            "pca_heatmap_shared_vst_provenance": "passed", "failed_enrichment_not_mislabeled_empty": "passed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--compare-to", help="Another real run under Real_DATA_TEST, for exact artifact reproducibility")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        output = local_path(ROOT, args.results_dir)
        logging.info("Verification: %s", verify(output))
        if args.compare_to:
            other = local_path(ROOT, args.compare_to)
            verify(other)
            folders = ("01_pca", "02_deg", "03_volcano", "04_heatmap")
            for folder in folders:
                first, second = read_json(output / folder / "summary.json"), read_json(other / folder / "summary.json")
                assert first["metrics"] == second["metrics"], f"Repeated metrics differ: {folder}"
                assert first["provenance"]["artifact_sha256"] == second["provenance"]["artifact_sha256"], f"Repeated artifact bytes differ: {folder}"
            logging.info("REPRODUCIBILITY_PASSED: All four successful Skills have identical metrics and artifact hashes across both runs.")
    except Exception as error:
        logging.error("REAL_OUTPUT_VERIFICATION_FAILED: %s", error)
        raise SystemExit(1)
