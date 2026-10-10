"""Real R exact tests on deliberately synthetic GO associations, not biological findings."""

import csv
import json
from pathlib import Path

import pytest

from bulk_rnaseq.skills.go_enrichment import run_go_enrichment


@pytest.mark.integration
@pytest.mark.parametrize("case", ["enriched", "empty", "ontology", "entire_universe"])
def test_real_go(case: str, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    source = Path(__file__).resolve().parents[2] / "examples" / "go"
    foreground = source / ("synthetic_universe.txt" if case == "entire_universe" else "synthetic_gene_list.txt")
    result = run_go_enrichment(foreground, source / "synthetic_mapping.tsv", tmp_path / "output",
                               universe=source / "synthetic_universe.txt", padj_threshold=0 if case == "empty" else .05,
                               ontology="BP" if case == "ontology" else "ALL",
                               rscript_executable=request.config.getoption("--rscript"))
    summary = json.loads(result.summary_json.read_text())
    assert summary["metrics"]["n_universe"] == 20
    assert summary["metrics"]["n_terms_tested"] == 3
    with (result.output_dir / "go_all_results.csv").open() as handle:
        rows = {r["go_id"]: r for r in csv.DictReader(handle)}
    hit = rows["GO:0006950"]
    # Independently known exact value for selecting all 5 of a designated set from 20.
    if case == "entire_universe":
        assert all(float(row["pvalue"]) == 1 for row in rows.values())
    else:
        assert float(hit["pvalue"]) == pytest.approx(1 / 15504)
        assert float(hit["padj"]) == pytest.approx(3 / 15504)  # Includes zero-overlap GO in BH family.
        assert int(rows["GO:0006281"]["overlap_count"]) == 0
    assert summary["metrics"]["n_enriched"] == (0 if case in {"empty", "entire_universe"} else 1)
    for name in summary["artifacts"].values():
        assert (result.output_dir / name).is_file()
    for name in ("go_dotplot.png", "go_barplot.png"):
        assert (result.output_dir / name).stat().st_size > 1000
    assert "R version" in summary["provenance"]["r_version"]
