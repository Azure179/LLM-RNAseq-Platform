"""Runtime assertions only; never compute or replace analysis results."""

import csv
import json
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from bulk_rnaseq.deg_data import read_deg_results
from bulk_rnaseq.enrichment_inputs import read_gene_list
from bulk_rnaseq.project_paths import PREFIX
from bulk_rnaseq.run_io import sha256
from bulk_rnaseq.workspace import ProjectError, ProjectManager
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workflow.history import ProjectHistory


def csv_records(path: Path) -> Iterator[dict[str, str]]:
    previous = csv.field_size_limit()
    try:
        csv.field_size_limit(max(previous, 2_000_000))
        with path.open(encoding="utf-8", newline="") as handle:
            yield from csv.DictReader(handle)
    finally:
        csv.field_size_limit(previous)


def csv_rows(path: Path) -> list[dict[str, str]]:
    return list(csv_records(path))


def check_enrichment(manager: ProjectManager, project: str, result: dict[str, Any],
                     source_run_id: str, subset: str) -> dict[str, Any]:
    context = manager.get_project_context(project)
    record = context.project_root / result["outputs"]["summary_json"]
    canonical = context.restore_paths(json.loads(record.read_text(encoding="utf-8")))
    assert canonical["metadata"]["provenance"]["engine"] == "clusterProfiler"
    provenance = context.restore_paths(json.loads((record.parent.parent / "provenance.json").read_text(encoding="utf-8")))
    history = ProjectHistory(manager, project)
    filtered, _ = history.artifact(source_run_id, "filtered_csv")
    full, _ = history.artifact(source_run_id, "deg_csv")
    expected = {row.original["gene_id"] for row in read_deg_results(filtered)
                if subset == "all" or subset == "up" and row.log2fc > 0 or subset == "down" and row.log2fc < 0}
    selected = Path(provenance["upstream_sources"]["gene_list"]["derived_path"])
    assert {row["gene_id"] for row in csv_rows(selected)} == expected
    assert provenance["upstream_sources"]["gene_list"]["source_run_id"] == source_run_id
    assert Path(provenance["upstream_sources"]["analysis_genes"]["path"]) == full
    eligible, _ = read_gene_list(full, analysis_background=True)
    # Verify exactly the engine's effective local annotation universe, not the species.
    kind = result["skill_name"].removeprefix("rnaseq.")
    mapped_genes: set[str] = set()
    names: dict[str, str] = {}
    for row in csv_records(record.parent / "mapping_used.csv"):
        mapped_genes.add(row["gene_id"])
        names[row["go_id" if kind == "go" else "pathway_id"]] = row["go_name" if kind == "go" else "pathway_name"]
    universe = {row["gene_id"] for row in csv_rows(record.parent / "universe_used.csv")}
    query = {row["gene_id"] for row in csv_rows(record.parent / "query_used.csv")}
    assert universe == mapped_genes <= eligible
    assert query == expected & universe
    table = csv_rows(record.parent / f"{kind}_all_results.csv")
    assert len(table) == result["summary"]["n_terms_tested"]
    assert len(csv_rows(record.parent / f"{kind}_enrichment.csv")) == result["summary"]["n_enriched"]
    assert all(row["description"] == names[row["go_id" if kind == "go" else "pathway_id"]] for row in table)
    return {"skill": result["skill_name"], "source_run_id": source_run_id, "subset": subset, "selected": len(expected),
            "mapped": len(query), "universe": len(universe), "eligible_background": len(eligible),
            "n_terms_tested": len(table), "n_enriched": result["summary"]["n_enriched"],
            "subset_verified": True, "background_verified": True, "term_names_verified": True}


def result_snapshot(manager: ProjectManager, project: str) -> dict[str, Any]:
    browser = ResultManager(manager, project)
    references = browser.list_result_refs()
    return {row.run_id: {"skill": row.skill_name, "workflow_id": row.workflow_id,
            "parents": row.parent_run_ids, "outputs": row.outputs,
            "hashes": {key: sha256(browser.validate_result_path(path)) for key, path in row.outputs.items()}}
            for row in references if row.status == "succeeded"}


def check_path_boundaries(manager: ProjectManager, project: str) -> dict[str, str]:
    context = manager.get_project_context(project)
    checks = {}
    for value in ("../Other/results/x.csv", "results/../input/x.csv", "/etc/passwd",
                  PREFIX + "Other:results/x.csv", r"C:\old_workspace\demo\results\x.csv"):
        try:
            context.resolve_path(value)
        except ProjectError as error:
            checks[value] = error.code
        else:
            raise AssertionError("Unsafe or unavailable path was accepted")
    assert checks[r"C:\old_workspace\demo\results\x.csv"] == "LEGACY_PATH_UNAVAILABLE"
    return checks


def check_previews(manager: ProjectManager, project: str) -> dict[str, int]:
    browser = ResultManager(manager, project)
    counts = {"images": 0, "tables": 0, "downloads": 0}
    for run in browser.list_runs(status="succeeded"):
        for artifact in run.artifacts:
            assert artifact.error_code is None, artifact
            if artifact.kind == "IMAGE":
                assert browser.preview_image(run.run_id, artifact.artifact_id).startswith(b"\x89PNG")
                counts["images"] += 1
            elif artifact.kind == "TABLE":
                assert len(browser.preview_table(run.run_id, artifact.artifact_id, limit=100).frame) <= 100
                counts["tables"] += 1
            else:
                continue
            _, data, _ = browser.download(run.run_id, artifact.artifact_id)
            assert len(data) == artifact.size_bytes
            counts["downloads"] += 1
    assert all(counts.values()), counts
    return counts
