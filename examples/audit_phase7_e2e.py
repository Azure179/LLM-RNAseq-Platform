"""Read-only evidence audit of UI-created Phase 7 runs. No API or Skill execution.

Writes only a separate audit document, never changes project results.
"""
import argparse
import csv
import json
import logging
from pathlib import Path

from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workflow.models import WorkflowResult
from bulk_rnaseq.workspace import FileImportService, ProjectManager
from bulk_rnaseq.workspace.chat_store import ChatStore
from bulk_rnaseq.workspace.result_manager import ResultManager


def table(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def audit(workspace: Path, key: str) -> dict:
    manager = ProjectManager(workspace)
    context = manager.get_project_context(key)
    browser = ResultManager(manager, key)
    messages = ChatStore(manager, key).history()
    turns = [row["result"] for row in messages if row["role"] == "assistant"]
    workflow_records, customizations, failures = [], [], []
    for path in sorted((context.output_root / "workflows").glob("*/workflow_result.json")):
        workflow = WorkflowResult.model_validate_json(path.read_text(encoding="utf-8"))
        record = {"workflow_id": workflow.workflow_id, "status": workflow.status,
                  "request": workflow.user_request, "execution_order": workflow.execution_order, "steps": {}}
        for step, item in workflow.step_records.items():
            result = workflow.step_results.get(step, {})
            metrics = {"skill": item["skill_name"], "status": item["status"], "run_id": item.get("run_id"),
                       "depends_on": item.get("depends_on", []), "summary": result.get("summary", {}),
                       "sample_selection": item.get("sample_selection"), "error_code": item.get("error_code"),
                       "started_at": item.get("started_at"), "finished_at": item.get("finished_at")}
            for name, checksum in item.get("output_sha256", {}).items():
                if sha256(Path(item["outputs"][name])) != checksum:
                    failures.append(f"{workflow.workflow_id}/{step}/{name}: output checksum differs")
            binding = item.get("upstream_sources", {}).get("gene_list")
            if binding and binding.get("gene_subset") is not None:
                raw = table(binding["path"])
                subset = binding["gene_subset"]
                expected = {r["gene_id"] for r in raw if subset == "all" or
                    (subset == "up" and float(r["log2FoldChange"]) > 0) or
                    (subset == "down" and float(r["log2FoldChange"]) < 0)}
                actual = {r["gene_id"] for r in table(binding["derived_path"])}
                matches = expected == actual
                metrics["subset_evidence"] = {"source_run_id": binding["run_id"], "subset": subset,
                    "n_expected": len(expected), "n_actual": len(actual), "exact_match": matches}
                if not matches:
                    failures.append(f"{workflow.workflow_id}/{step}: gene subset differs")
            record["steps"][step] = metrics
        final = path.parent / "final_response.json"
        if final.exists():
            data = json.loads(final.read_text(encoding="utf-8"))
            record["final"] = {k: data.get(k) for k in ("status", "grounded", "model", "usage", "final_response")}
        workflow_records.append(record)
    for path in sorted((context.output_root / "customizations").glob("*/customization.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = {k: data.get(k) for k in ("customization_id", "status", "source_run_id", "source_skill", "user_request", "changed_parameters", "model", "usage")}
        result = data.get("skill_result") or {}
        entry.update(run_id=result.get("run_id"), summary=result.get("summary"), warnings=result.get("warnings"))
        protected = all(sha256(Path(b["source_path"])) == b["sha256"] for b in data.get("input_bindings", {}).values())
        entry["source_hashes_unchanged"] = protected
        if not protected:
            failures.append(f"{data['customization_id']}: original input changed")
        if data.get("source_skill") == "rnaseq.filter_by_annotation" and data.get("status") == "succeeded":
            source = table(data["input_bindings"]["filtered_source.csv"]["source_path"])
            output = table(data["outputs"]["filtered_table"])
            entry["same_gene_set"] = {r["gene_id"] for r in source} == {r["gene_id"] for r in output}
            if data["changed_parameters"] == {"order_by": "log2fc", "descending": True}:
                values = [float(r["log2FoldChange"]) for r in output]
                entry["descending_log2fc"] = values == sorted(values, reverse=True)
                if not entry["same_gene_set"] or not entry["descending_log2fc"]:
                    failures.append(f"{data['customization_id']}: sorting changed selection/order")
        customizations.append(entry)
    return {"project": key, "inputs": {role: FileImportService(manager).get_imported_file_info(key, role)
        for role in ("count_matrix", "metadata", "go_mapping", "kegg_mapping")},
        "workflows": workflow_records, "customizations": customizations,
        "chat": [{k: row.get(k) for k in ("request_id", "user_message", "status", "error_code", "workflow_id", "customization_id", "source_run_id", "request_count", "usage", "duration_seconds")} for row in turns],
        "cost": {"request_count": sum(row.get("request_count", 0) for row in turns),
                 **{key: sum(row.get("usage", {}).get(key, 0) for row in turns)
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")}},
        "n_browser_runs": len(browser.list_runs()), "integrity_failures": failures,
        "note": "Structural/checksum audit is not a semantic PASS. See PHASE7_E2E_REPORT.md for known bad historical runs and UI observations."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--project", default="Real_DATA_PHASE7_E2E")
    parser.add_argument("--output", type=Path, default=Path("outputs/phase7_e2e/audit.json"))
    args = parser.parse_args()
    evidence = audit(args.workspace, args.project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, evidence)
    logging.basicConfig(level=logging.INFO)
    logging.info("Audit saved to %s; integrity failures=%d; cost=%s", args.output, len(evidence["integrity_failures"]), evidence["cost"])
