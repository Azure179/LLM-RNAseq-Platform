"""Explicit Linux/container smoke: real Skills, no DeepSeek, mounted inputs only."""

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from importlib.metadata import version
import shutil

import httpx

from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import FileImportService, ProjectManager
from bulk_rnaseq.workspace.result_resolver import ResultResolver
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workflow.history import ProjectHistory
from bulk_rnaseq.workflow.models import WorkflowPlan
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.customization.engine import CustomizationEngine
from bulk_rnaseq.customization.models import CustomizationDecision
from bulk_rnaseq.customization.planner import CustomizationPlan
from bulk_rnaseq.llm.client import ChatResponse
from bulk_rnaseq.project_paths import PREFIX
from runtime_checks import check_enrichment, check_path_boundaries, check_previews, result_snapshot

LOGGER = logging.getLogger("container.smoke")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "verify-reuse"))
    parser.add_argument("--project", default="Container_Phase9_E2E")
    parser.add_argument("--inputs", type=Path, default=Path("/smoke-input"))
    parser.add_argument("--counts", default="count_matrix.all_samples.csv")
    parser.add_argument("--metadata", default="metadata.all_samples.csv")
    parser.add_argument("--go", default="gene_to_go.tsv")
    parser.add_argument("--kegg", default="gene_to_kegg.tsv")
    parser.add_argument("--treatment", default="OT")
    parser.add_argument("--control", default="T")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    report: dict = {"status": "failed", "phase": args.mode, "steps": []}
    report_path: Path | None = None
    try:
        if sys.platform != "linux" or os.geteuid() == 0:
            raise RuntimeError("This smoke must run in the non-root Linux container.")
        if sys.version_info[:3] != (3, 13, 2):
            raise RuntimeError("Expected audited Python 3.13.2.")
        root = Path(os.environ["RNASEQ_WORKSPACE_ROOT"]).resolve()
        if root != Path("/workspace"):
            raise RuntimeError("Container persistence contract requires /workspace.")
        manager = ProjectManager(root)
        if args.mode == "run":
            snapshot = manager.create_project(args.project, description="Phase 9 Linux runtime, mounted real data")
        else:
            snapshot = manager.open_project(args.project)
        report_path = snapshot.root / "reports" / ("container_smoke.json" if args.mode == "run" else "container_restart.json")
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=1800, trust_env=False) as client:
            for endpoint in ("/health", "/skills"):
                response = client.get(endpoint)
                response.raise_for_status()
                report[endpoint] = response.json()
            if args.mode == "run":
                security = []
                for endpoint, body, expected in (
                    ("/skills/rnaseq.unknown/run", {"project_id": args.project, "parameters": {}}, 404),
                    ("/skills/rnaseq.deg/run", {"project_id": args.project, "parameters": {"output_dir": "/etc/private"}}, 422),
                    ("/skills/rnaseq.deg/run", {"project_id": args.project, "parameters": {}, "api_key": "private-sentinel"}, 422)):
                    response = client.post(endpoint, json=body)
                    assert response.status_code == expected
                    assert not any(value in response.text for value in ("/etc/private", "private-sentinel", "Traceback", "/workspace/"))
                    security.append({"endpoint": endpoint, "status": response.status_code})
                report["http_security"] = security

            def record_result(result: dict) -> None:
                skill = result["skill_name"]
                if result["status"] != "succeeded":
                    raise RuntimeError(f"{skill}: {result.get('error_code')}")
                for relative in result["outputs"].values():
                    path = snapshot.root / relative
                    if Path(relative).is_absolute() or not path.resolve().is_relative_to(snapshot.root) or not path.is_file():
                        raise RuntimeError("Nonportable or missing API artifact")
                client.get(f"/projects/{args.project}/results/{result['run_id']}").raise_for_status()

            def execute(skill: str, parameters: dict, source: str | None = None) -> dict:
                started = perf_counter()
                body = {"project_id": args.project, "parameters": parameters}
                if source is not None:
                    body["source_run_id"] = source
                response = client.post(f"/skills/{skill}/run", json=body)
                response.raise_for_status()
                result = response.json()
                record_result(result)
                if skill in {"rnaseq.go", "rnaseq.kegg"}:
                    canonical = json.loads((snapshot.root / result["outputs"]["summary_json"]).read_text(encoding="utf-8"))
                    if canonical["metadata"]["provenance"]["engine"] != "clusterProfiler":
                        raise RuntimeError("Unexpected enrichment engine")
                report["steps"].append({"skill": skill, "seconds": perf_counter()-started, "result": result})
                write_json(report_path, report)
                LOGGER.info("%s succeeded run_id=%s", skill, result["run_id"])
                return result

            if args.mode == "run":
                report["runtime"] = {"python": sys.version, "uid": os.geteuid(), "gid": os.getegid(),
                    "rscript": shutil.which("Rscript"), "encoding": sys.getfilesystemencoding(),
                    "packages": {name: version(name) for name in ("fastapi", "pydantic", "httpx", "pandas")}}
                unicode_project = manager.create_project(args.project + "_中文", description="UTF-8 路径和元数据写入验证")
                assert manager.open_project(unicode_project.key).metadata.description == "UTF-8 路径和元数据写入验证"
                assert not os.access("/app", os.W_OK) and os.access(snapshot.root, os.W_OK)
                assert not any((Path("/app") / name).exists() for name in (".env", "workspace", "Real_DATA_TEST", ".venv"))
                report["nonroot_utf8_writes"] = True
                for command in (["python", "--version"], ["R", "--version"], ["Rscript", "--version"],
                        ["Rscript", "--vanilla", "/opt/rnaseq/container/audit_runtime.R", str(snapshot.root / "reports/r-runtime.json")],
                        ["Rscript", "--vanilla", "-e", "library(DESeq2); library(clusterProfiler); cat('LIBRARIES_LOADED\\n')"]):
                    process = subprocess.run(command, capture_output=True, text=True, timeout=120)
                    report.setdefault("processes", []).append({"command": command, "returncode": process.returncode,
                        "stdout": process.stdout, "stderr": process.stderr})
                    if process.returncode != 0:
                        raise RuntimeError("Runtime smoke process failed; inspect report")
                importer = FileImportService(manager)
                for role, name in (("count_matrix", args.counts), ("metadata", args.metadata),
                                   ("go_mapping", args.go), ("kegg_mapping", args.kegg)):
                    started = perf_counter()
                    result = importer.import_file(args.project, args.inputs / name, role)
                    report.setdefault("imports", {})[role] = {"status": result.status, "summary": result.summary,
                        "warnings": result.warnings, "seconds": perf_counter() - started}
                    write_json(report_path, report)
                    if result.status != "succeeded":
                        raise RuntimeError(f"Import failed: {role}")
                # Use the verified analysis-copy column, not the source file's header.
                info = importer.get_imported_file_info(args.project, "metadata")
                execute("rnaseq.pca", {"group_column": info["summary"]["group_column"]})
                # Explicit test plan, not a purported DeepSeek response. All computations are real.
                binding = {"source": "workflow_step", "step_id": "deg", "output": "deg_csv"}
                plan = WorkflowPlan(project_id=args.project, user_request="Explicit container integration plan; no model invoked",
                    steps=[{"step_id": "deg", "skill_name": "rnaseq.deg", "parameters": {
                        "treatment": args.treatment, "control": args.control}},
                        {"step_id": "volcano", "skill_name": "rnaseq.volcano", "input_bindings": {"deg_result_file": binding}},
                        {"step_id": "heatmap", "skill_name": "rnaseq.heatmap", "parameters": {"top_n": 50,
                            "group_column": info["summary"]["group_column"]}, "input_bindings": {"deg_result_file": binding}}])
                started = perf_counter()
                response = client.post("/workflow/run", json={"project_id": args.project, "plan": plan.model_dump(mode="json")})
                response.raise_for_status()
                workflow = response.json()
                report["workflow"] = workflow
                report["workflow_seconds"] = perf_counter() - started
                write_json(report_path, report)
                if workflow["status"] != "succeeded":
                    raise RuntimeError("Real multi-Skill workflow failed; inspect its canonical results")
                for result in workflow["step_results"].values():
                    record_result(result)
                deg = workflow["step_results"]["deg"]
                report["deg_run_id"] = deg["run_id"]
                source, checksum = ProjectHistory(manager, args.project).artifact(deg["run_id"], "deg_csv")
                for subset in ("all", "up", "down"):
                    for kind in ("go", "kegg"):
                        parameters = {"gene_subset": subset, **({"ontology": "BP"} if kind == "go" else {})}
                        result = execute("rnaseq." + kind, parameters, deg["run_id"])
                        report.setdefault("enrichment_checks", []).append(check_enrichment(manager, args.project, result, deg["run_id"], subset))
                # Explicit local customization fixture, not mocked statistics or an invented LLM decision.
                custom_plan = CustomizationPlan(CustomizationDecision(mode="parameter_override", target_skill="rnaseq.volcano",
                    parameters={"top_n_labels": 5, "title": "Container customization verification"}),
                    ChatResponse("local-validation-fixture", "NOT_RUN", "", "stop", None, 0.),
                    "Deterministic runtime test: change Volcano display only; no DeepSeek call")
                custom = CustomizationEngine(manager, args.project, create_default_registry()).execute(custom_plan, source_run_id=deg["run_id"])
                report["customization"] = custom
                assert custom["status"] == "succeeded", custom.get("error_code")
                assert sha256(source) == checksum
                report["snapshot"] = result_snapshot(manager, args.project)
                assert sum(row["skill"] == "rnaseq.deg" for row in report["snapshot"].values()) == 1
                for row in report["snapshot"].values():
                    if row["skill"] in {"rnaseq.volcano", "rnaseq.heatmap", "rnaseq.go", "rnaseq.kegg"}:
                        assert row["parents"] == [deg["run_id"]]
                report["path_boundaries"] = check_path_boundaries(manager, args.project)
                report["previews"] = check_previews(manager, args.project)
                raw = (snapshot.root / "results/workflows" / workflow["workflow_id"] / "workflow_result.json").read_text(encoding="utf-8")
                assert PREFIX in raw
                report["portable_records"] = True
                other = manager.create_project(args.project + "_Isolation")
                response = client.get(f"/projects/{other.key}/results/{deg['run_id']}")
                assert response.status_code == 404
                report["project_isolation"] = True
            else:
                previous = manager.get_project_context(args.project).restore_paths(json.loads((snapshot.root / "reports/container_smoke.json").read_text(encoding="utf-8")))
                if previous["status"] != "passed":
                    raise RuntimeError("Initial smoke did not pass")
                before = result_snapshot(manager, args.project)
                assert before == previous["snapshot"], "Persisted artifacts or lineage changed"
                custom = ResultManager(manager, args.project).list_collections("Customization")
                assert any(row["id"] == previous["customization"]["customization_id"] for row in custom)
                identity = previous["deg_run_id"]
                resolution = ResultResolver(manager, args.project).find_compatible_result(
                    skill_name="rnaseq.deg", run_id=identity, required_outputs=("deg_csv", "filtered_csv"))
                if resolution.status != "resolved":
                    raise RuntimeError("Persisted DEG failed resolver verification")
                source, checksum = ProjectHistory(manager, args.project).artifact(identity, "deg_csv")
                execute("rnaseq.volcano", {}, identity)
                if sha256(source) != checksum:
                    raise RuntimeError("Reused DEG was modified")
                report["reused_deg_run_id"] = identity
                report["restored_runs"] = len(before)
                after = result_snapshot(manager, args.project)
                assert sum(row["skill"] == "rnaseq.deg" for row in after.values()) == 1
                assert len(after) == len(before) + 1
                report["previews"] = check_previews(manager, args.project)
            report["status"] = "passed"
        LOGGER.info("Container %s passed", args.mode)
        return 0
    except Exception as error:
        report["error_type"] = type(error).__name__
        report["error_code"] = getattr(error, "code", None)
        # This probe never receives credentials; retain a bounded local diagnostic.
        report["error_message"] = str(error)[:1000]
        if isinstance(error, httpx.HTTPStatusError):
            report["http_error"] = {"status": error.response.status_code, "body": error.response.json()}
        LOGGER.error("Container smoke failed (%s); inspect its report, no success assumed.", type(error).__name__)
        return 1
    finally:
        if report_path is not None:
            write_json(report_path, report)


if __name__ == "__main__":
    raise SystemExit(main())
