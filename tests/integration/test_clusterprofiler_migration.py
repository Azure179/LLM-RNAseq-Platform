"""Actual R engine comparison on synthetic fixtures; no API or research claims."""

import csv
import json
from pathlib import Path
import subprocess

import pytest

from bulk_rnaseq.skills.go_enrichment import run_go_enrichment
from bulk_rnaseq.skills.kegg_enrichment import run_kegg_enrichment
from bulk_rnaseq.customization.enrichment_plot import replot_enrichment
from bulk_rnaseq.customization.models import EnrichmentPlotOptions
from bulk_rnaseq.run_io import sha256


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["go", "kegg"])
@pytest.mark.parametrize("subset", ["all", "up", "down"])
def test_native_engine_matches_legacy_probabilities_not_legacy_bh(
    kind: str, subset: str, tmp_path: Path, request: pytest.FixtureRequest,
) -> None:
    # >500-wide set, single-member set and slash-containing ID catch default-size
    # filtering and unsafe splitting of clusterProfiler geneID strings.
    genes = ["g/1", *[f"g{i}" for i in range(2, 601)]]
    selected = {"up": genes[:5], "down": genes[5:10], "all": genes[:10]}[subset]
    query, background, mapping = (tmp_path / name for name in ("query.txt", "background.txt", "mapping.tsv"))
    query.write_text("\n".join([*selected, selected[0], "unmapped"]), encoding="utf-8")
    background.write_text("\n".join([*genes, "unmapped", genes[0]]), encoding="utf-8")
    ids = ["GO:0006950", "GO:0006281", "GO:0008150", "GO:0000001"] if kind == "go" else ["map00010", "map00020", "map01100", "map00001"]
    header = ["gene_id", "go_id", "go_name", "ontology"] if kind == "go" else ["gene_id", "pathway_id", "pathway_name"]
    with mapping.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(header)
        for term, members in zip(ids, (genes[:5], genes[5:10], genes, genes[:1])):
            for gene in [*members, members[0], "annotation_only"]:
                writer.writerow([gene, term, "Synthetic set " + term, *(["BP"] if kind == "go" else [])])
    skill = run_go_enrichment if kind == "go" else run_kegg_enrichment
    executable = request.config.getoption("--rscript")
    result = skill(query, mapping, tmp_path / "out", universe=background, rscript_executable=executable)
    summary = json.loads(result.summary_json.read_text(encoding="utf-8"))
    assert summary["metrics"]["n_query_input"] == len(selected) + 1
    assert summary["metrics"]["n_query_mapped"] == len(selected)
    assert summary["metrics"]["n_universe"] == 600
    assert summary["provenance"]["maxGSSize"] == 600
    assert summary["provenance"]["minGSSize"] == 1
    assert summary["provenance"]["clusterProfiler_version"]
    assert summary["metrics"]["n_query_unmapped"] == 1
    assert summary["metrics"]["n_background_unmapped"] == 1
    codes = {w["code"] for w in summary["warnings"]}
    assert {"DUPLICATE_ASSOCIATIONS", "DUPLICATE_QUERY_GENES", "UNMAPPED_GENES"} <= codes
    reference = tmp_path / "legacy.csv"
    process = subprocess.run([executable, str(Path(__file__).parents[1] / "fixtures/legacy_ora_reference.R"),
        str(result.output_dir / "query_used.csv"), str(result.output_dir / "universe_used.csv"),
        str(result.output_dir / "mapping_used.csv"), str(reference)], capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, {"stdout": process.stdout, "stderr": process.stderr}
    with reference.open() as handle:
        legacy = {row["id"]: row for row in csv.DictReader(handle)}
    with (result.output_dir / f"{kind}_all_results.csv").open() as handle:
        native = {row[header[1]]: row for row in csv.DictReader(handle)}
    assert set(native) == {key for key, row in legacy.items() if int(row["overlap"]) > 0}
    assert ids[2] in native  # A 600-gene set is not silently excluded at 500.
    for key, row in native.items():
        assert float(row["pvalue"]) == pytest.approx(float(legacy[key]["pvalue"]), rel=1e-9, abs=1e-300)
        assert float(row["padj"]) == pytest.approx(float(legacy[key]["positive_family_padj"]), rel=1e-9, abs=1e-300)
        assert float(row["padj"]) <= float(legacy[key]["padj"]) + 1e-12
    source = result.output_dir / f"{kind}_enrichment.csv"
    before = sha256(source)
    destination = tmp_path / "customized"
    destination.mkdir()
    plotted = replot_enrichment(source, destination, kind, EnrichmentPlotOptions(top_n=2),
                                rscript_executable=executable)
    assert plotted["n_available"] == summary["metrics"]["n_enriched"]
    assert sha256(source) == before
