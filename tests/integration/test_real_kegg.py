"""Real offline R KEGG ORA on synthetic associations; not biological evidence."""

import csv
import json
from pathlib import Path

import pytest

from bulk_rnaseq.skills.kegg_enrichment import run_kegg_enrichment


@pytest.mark.integration
@pytest.mark.parametrize("case", ["normal", "empty", "whole_background", "eggnog", "deg_background"])
def test_real_kegg(case: str, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    source = Path(__file__).resolve().parents[2] / "examples" / "kegg"
    mapping = source / "synthetic_mapping.tsv"
    if case == "eggnog":
        mapping = tmp_path / "synthetic.emapper.annotations"
        mapping.write_text("## synthetic associations\n#query\tKEGG_Pathway\n" + "".join(
            f"g{i}\tko01100,map01100," + ("ko00010,map00010" if i <= 5 else "ko00020,map00020") + "\n"
            for i in range(1, 21)))
    query = source / ("synthetic_universe.txt" if case == "whole_background" else "synthetic_gene_list.txt")
    background_options = {"universe": source / "synthetic_universe.txt"}
    if case == "deg_background":
        deg = tmp_path / "full_deg_fixture.csv"
        deg.write_text("gene_id,log2FoldChange,pvalue,padj\n" +
                       "".join(f"g{i},2,0.01,0.02\n" for i in range(1, 21)) + "filtered_out,0,0.9,NA\n")
        background_options = {"analysis_genes": deg}
    result = run_kegg_enrichment(query, mapping, tmp_path / "out", **background_options,
                                 padj_threshold=0 if case == "empty" else .05,
                                 rscript_executable=request.config.getoption("--rscript"))
    summary = json.loads(result.summary_json.read_text())
    with (result.output_dir / "kegg_all_results.csv").open() as handle:
        rows = {r["pathway_id"]: r for r in csv.DictReader(handle)}
    assert len(rows) == 3 and summary["metrics"]["n_universe"] == 20
    if case == "whole_background":
        assert all(float(r["pvalue"]) == 1 for r in rows.values())
    else:
        assert float(rows["map00010"]["pvalue"]) == pytest.approx(1 / 15504)
        assert float(rows["map00010"]["padj"]) == pytest.approx(3 / 15504)
        assert int(rows["map00020"]["overlap_count"]) == 0
    assert summary["metrics"]["n_enriched"] == (0 if case in {"empty", "whole_background"} else 1)
    if case == "eggnog":
        assert summary["metrics"]["n_mapping_pairs"] == 40  # Not 80 from ko/map duplicate representations.
    if case == "deg_background":
        assert summary["methods"]["background_source"] == "analysis_genes"
        assert summary["metrics"]["n_background_rows_excluded"] == 1
    for name in summary["artifacts"].values():
        assert (result.output_dir / name).is_file()
    for name in ("kegg_dotplot.png", "kegg_barplot.png"):
        assert (result.output_dir / name).stat().st_size > 1000
    assert "R version" in summary["provenance"]["r_version"]
