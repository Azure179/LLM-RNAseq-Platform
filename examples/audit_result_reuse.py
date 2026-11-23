"""Read-only hotfix evidence audit. No model calls, result editing or Skill execution."""

import argparse
import csv
import json
from pathlib import Path

from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import ProjectManager
from bulk_rnaseq.workspace.chat_store import ChatStore
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.result_resolver import ResultResolver
from bulk_rnaseq.workflow.history import ProjectHistory


def audit(workspace: Path, project: str, baseline_request: str) -> dict:
    manager = ProjectManager(workspace)
    browser = ResultManager(manager, project)
    rows = [row["result"] for row in ChatStore(manager, project).history() if row["role"] == "assistant"]
    start = next(i for i, row in enumerate(rows) if row["request_id"] == baseline_request)
    rows = rows[start:]
    baseline = rows[0]
    deg_ids = [s["run_id"] for s in baseline["steps"] if s["skill_name"] == "rnaseq.deg" and s["status"] == "succeeded"]
    assert len(deg_ids) == 1
    baseline_deg = deg_ids[0]
    history = ProjectHistory(manager, project)
    full, checksum = history.artifact(baseline_deg, "deg_csv")
    filtered, _ = history.artifact(baseline_deg, "filtered_csv")
    with filtered.open(encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    report = {"project": project, "baseline_request": baseline_request, "baseline_deg": baseline_deg,
        "baseline_deg_sha256": checksum, "reuse": [], "filtered_sorts": [], "requests": [], "usage": {},
        "additional_deg_executions": sum(s["skill_name"] == "rnaseq.deg" for r in rows[1:] for s in r.get("steps", []))}
    assert report["additional_deg_executions"] == 0
    for row in rows:
        report["requests"].append({key: row.get(key) for key in ("request_id", "user_message", "status", "request_type",
            "workflow_id", "run_id", "source_run_id", "error_code", "request_count", "duration_seconds", "usage")})
        for key, value in row.get("usage", {}).items():
            if isinstance(value, int):
                report["usage"][key] = report["usage"].get(key, 0) + value
        if (row.get("request_type") == "customization" and row["status"] == "succeeded"
                and row.get("changed_parameters", {}).get("order_by") == "log2fc"):
            source_run = browser.get_run(row["source_run_id"])
            assert source_run.skill_name == "rnaseq.filter_by_annotation"
            source_path = browser.validate_result_path(browser.get_artifact(source_run.run_id, "filtered_table").relative_path)
            target_path = browser.validate_result_path(browser.get_artifact(row["run_id"], "filtered_table").relative_path)
            with source_path.open(encoding="utf-8-sig", newline="") as handle:
                source_rows = list(csv.DictReader(handle))
            with target_path.open(encoding="utf-8-sig", newline="") as handle:
                target_rows = list(csv.DictReader(handle))
            assert {r["gene_id"] for r in source_rows} == {r["gene_id"] for r in target_rows}
            values = [float(r["log2FoldChange"]) for r in target_rows]
            assert values == sorted(values, reverse=True)
            report["filtered_sorts"].append({"run_id": row["run_id"], "source_run_id": source_run.run_id,
                "n_genes": len(target_rows), "same_gene_set": True, "descending_log2fc": True})
        if row.get("request_type") != "result_reuse" or row["status"] != "succeeded":
            continue
        workflow_path = browser.root / "workflows" / row["workflow_id"] / "workflow_result.json"
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        assert len(workflow["step_records"]) == 1
        record = next(iter(workflow["step_records"].values()))
        assert record["skill_name"] != "rnaseq.deg"
        parents = record["upstream_sources"]
        assert row["source_run_id"] == baseline_deg
        item = {"run_id": row["run_id"], "skill_name": record["skill_name"], "source_run_id": baseline_deg,
                "summary": next(iter(workflow["step_results"].values()))["summary"], "files_exist": True}
        for value in record["outputs"].values():
            assert browser.validate_result_path(value).is_file()
        for source in parents.values():
            if source["source"] == "project_result":
                assert source["run_id"] == baseline_deg and sha256(Path(source["path"])) == source["sha256"]
        if "gene_list" in parents:
            source = parents["gene_list"]
            subset = source["gene_subset"]
            expected = {r["gene_id"] for r in records if subset == "all"
                or (subset == "up" and float(r["log2FoldChange"]) > 0)
                or (subset == "down" and float(r["log2FoldChange"]) < 0)}
            with Path(source["derived_path"]).open(encoding="utf-8", newline="") as handle:
                actual = {r["gene_id"] for r in csv.DictReader(handle)}
            assert actual == expected
            assert Path(parents["analysis_genes"]["path"]) == full
            item.update(gene_subset=subset, selected_genes=len(actual), exact_gene_ids=True, full_deg_background_source=True)
        report["reuse"].append(item)
    resolver = ResultResolver(manager, project)
    options = dict(skill_name="rnaseq.deg", required_outputs=("deg_csv",), treatment="OT", control="T")
    assert resolver.find_compatible_result(**options).status == "ambiguous"
    assert resolver.find_compatible_result(**options, selection="latest").result.run_id == baseline_deg
    recent = resolver.find_compatible_result(**options, selection="recent")
    assert recent.status == "resolved"
    # A subsequent customization can legitimately move chat lineage to an older
    # DEG. Verify its actual ancestry rather than equating 'recent' with 'latest'.
    refs = {ref.run_id: ref for ref in browser.list_result_refs()}
    last_success = next(row for row in reversed(rows) if row["status"] == "succeeded")
    pending = {last_success.get("run_id"), *[s.get("run_id") for s in last_success.get("steps", [])]} - {None}
    ancestors: set[str] = set()
    while pending:
        level = pending - ancestors
        ancestors.update(level)
        pending = {parent for key in level if key in refs for parent in refs[key].parent_run_ids}
    assert recent.result.run_id in ancestors
    report["recent_deg_run_id"] = recent.result.run_id
    assert resolver.find_compatible_result(skill_name="rnaseq.deg", treatment="T", control="OT").status == "not_found"
    report["resolver_checks"] = "ambiguous/latest/recent/direction passed"
    report["source_preserved"] = sha256(full) == checksum
    report["request_count"] = sum(r.get("request_count", 0) for r in rows)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--baseline-request", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.workspace, args.project, args.baseline_request)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)


if __name__ == "__main__":
    main()
