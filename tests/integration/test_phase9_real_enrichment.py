"""Opt-in real private data regression, reusing a verified historical OT/T DEG.

Creates new Workflow run directories only; never overwrites previous evidence.
Missing private project is a SKIP, never replaced with synthetic findings.
"""

import csv
import json
from pathlib import Path
import subprocess
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from bulk_rnaseq.api.main import create_app
from bulk_rnaseq.deg_data import read_deg_results
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workflow.history import ProjectHistory
from bulk_rnaseq.workspace import ProjectManager
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.result_resolver import ResultResolver


@pytest.mark.integration
def test_real_ot_t_enrichment_migration(request: pytest.FixtureRequest) -> None:
    root = Path(__file__).resolve().parents[2]
    project = "Real_DATA_PHASE7_E2E"
    workspace = root / "workspace"
    if not (workspace / project / "project.json").is_file():
        pytest.skip("Private real-data project absent; no substitute data used.")
    manager = ProjectManager(workspace)
    resolution = ResultResolver(manager, project).find_compatible_result(
        skill_name="rnaseq.deg", treatment="OT", control="T", selection="latest",
        required_outputs=("deg_csv", "filtered_csv"))
    assert resolution.status == "resolved", resolution
    source = resolution.result
    history = ProjectHistory(manager, project)
    filtered, checksum = history.artifact(source.run_id, "filtered_csv")
    full, full_checksum = history.artifact(source.run_id, "deg_csv")
    deg = read_deg_results(filtered)
    report_dir = root / "outputs/phase9_enrichment" / uuid4().hex
    report_dir.mkdir(parents=True)
    report = {"status": "running", "source_run_id": source.run_id,
        "filtered_sha256": checksum, "full_sha256": full_checksum, "comparison": "OT_vs_T", "runs": []}
    rscript = request.config.getoption("--rscript")
    try:
        with TestClient(create_app(workspace_root=workspace, timeout=1800, rscript_executable=rscript)) as client:
            for subset in ("all", "up", "down"):
                expected = {r.original["gene_id"] for r in deg if subset == "all"
                    or subset == "up" and r.log2fc > 0 or subset == "down" and r.log2fc < 0}
                for kind in ("go", "kegg"):
                    started = perf_counter()
                    parameters = {"gene_subset": subset, **({"ontology": "BP"} if kind == "go" else {})}
                    response = client.post(f"/skills/rnaseq.{kind}/run", json={"project_id": project,
                        "source_run_id": source.run_id, "parameters": parameters})
                    entry = {"skill": kind, "gene_subset": subset, "seconds": perf_counter() - started,
                        "http_status": response.status_code, "response": response.json()}
                    report["runs"].append(entry)
                    write_json(report_dir / "report.json", report)
                    assert response.status_code == 200, response.text
                    result = response.json()
                    assert result["status"] == "succeeded"
                    record = manager.root / project / result["outputs"]["summary_json"]
                    summary = json.loads(record.read_text(encoding="utf-8"))
                    provenance = summary["metadata"]["provenance"]
                    assert provenance["engine"] == "clusterProfiler"
                    output = record.parent
                    state = json.loads((output.parent / "provenance.json").read_text(encoding="utf-8"))
                    selected_path = Path(state["upstream_sources"]["gene_list"]["derived_path"])
                    with selected_path.open(encoding="utf-8") as handle:
                        actual = {r["gene_id"] for r in csv.DictReader(handle)}
                    assert actual == expected
                    assert state["upstream_sources"]["gene_list"]["source_run_id"] == source.run_id
                    assert Path(state["upstream_sources"]["analysis_genes"]["path"]) == full
                    # Results Browser and HTTP detail consume the unchanged CSV contract.
                    browser = ResultManager(manager, project)
                    assert browser.get_run(result["run_id"]).status == "succeeded"
                    assert client.get(f"/projects/{project}/results/{result['run_id']}").status_code == 200
                    baseline = report_dir / f"{kind}_{subset}_legacy.csv"
                    execution = subprocess.run([rscript, str(root / "tests/fixtures/legacy_ora_reference.R"),
                        str(output / "query_used.csv"), str(output / "universe_used.csv"),
                        str(output / "mapping_used.csv"), str(baseline)], capture_output=True, text=True, timeout=300)
                    assert execution.returncode == 0, {"stdout": execution.stdout, "stderr": execution.stderr}
                    with baseline.open(encoding="utf-8") as handle:
                        legacy = {r["id"]: r for r in csv.DictReader(handle)}
                    with (output / f"{kind}_all_results.csv").open(encoding="utf-8") as handle:
                        # Large real gene lists require the same bounded accommodation as production.
                        limit = csv.field_size_limit()
                        try:
                            csv.field_size_limit(max(limit, 2_000_000))
                            native = list(csv.DictReader(handle))
                        finally:
                            csv.field_size_limit(limit)
                    for row in native:
                        old = legacy[row["go_id" if kind == "go" else "pathway_id"]]
                        assert float(row["pvalue"]) == pytest.approx(float(old["pvalue"]), rel=1e-8, abs=1e-300)
                        assert float(row["padj"]) == pytest.approx(float(old["positive_family_padj"]), rel=1e-8, abs=1e-300)
                    pcut = .05 if kind == "go" else 1
                    entry["comparison"] = {
                        "old_terms_tested": len(legacy), "new_terms_tested": len(native),
                        "old_significant": sum(int(r["overlap"]) > 0 and float(r["pvalue"]) <= pcut and float(r["padj"]) <= .05 for r in legacy.values()),
                        "new_significant": result["summary"]["n_enriched"],
                        "pvalues_match": True, "native_bh_matches_positive_family": True,
                        "selected_gene_ids_match_verified_deg": True}
            assert sha256(filtered) == checksum and sha256(full) == full_checksum
            report["status"] = "passed"
    finally:
        write_json(report_dir / "report.json", report)
