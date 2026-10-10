"""Explicit real-data workspace import test; does not call DeepSeek or R.

Run from the repository root: python examples/workspace_import_test.py
Existing projects and all Real_DATA_TEST source files are never overwritten.
"""

import argparse
import json
import logging
from pathlib import Path
import time

from bulk_rnaseq.logging_config import run_logger
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import FileImportService, ProjectError, ProjectManager


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default="Real_DATA_UI_TEST")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manager = ProjectManager(root / "workspace")
    try:
        project = manager.create_project(options.project_name, "Phase 6.5.2 real file import test; no RNA-seq analysis.")
    except ProjectError as error:
        logging.error("%s: %s (choose a new --project-name for a rerun)", error.code, error)
        return 1
    service = FileImportService(manager)
    prepared = root / "Real_DATA_TEST/input/prepared_asc"
    sources = {"count_matrix": prepared / "count_matrix.csv", "metadata": prepared / "metadata.normalized.csv",
               "go_mapping": prepared / "gene_to_go.tsv", "kegg_mapping": prepared / "gene_to_kegg.tsv"}
    report = {"phase": "6.5.2", "status": "failed", "project": project.key, "imports": {},
              "source_preparation": "Existing prepared_asc outputs, with the previously verified transcript-to-gene mapping. This importer does not change gene IDs.",
              "r_calls": 0, "deepseek_calls": 0}
    with run_logger(project.root / "logs/real_import_test.log", project.key) as logger:
        started = time.perf_counter()
        try:
            for role, source in sources.items():
                before = sha256(source)
                step = time.perf_counter()
                logger.info("Real import started: role=%s bytes=%s", role, source.stat().st_size)
                result = service.import_file(project.key, source, role)
                report["imports"][role] = {"result": result.model_dump(mode="json", exclude={"context"}),
                                           "seconds": round(time.perf_counter() - step, 3),
                                           "source": source.relative_to(root).as_posix(), "source_sha256": before,
                                           "source_unchanged": before == sha256(source)}
                logger.info("Real import finished: role=%s status=%s seconds=%s", role, result.status, report["imports"][role]["seconds"])
                if result.status != "succeeded":
                    raise ProjectError(result.error_code or "IMPORT_FAILED", result.message)
            reopened = FileImportService(ProjectManager(manager.root))
            # Explicit existing comparison config; never infer direction from row order.
            config = json.loads((root / "Real_DATA_TEST/config/real_test_config.json").read_text(encoding="utf-8"))
            confirmed = {"rnaseq.deg": {"treatment": config["treatment"], "control": config["control"]}}
            report["confirmed_parameters"] = confirmed
            report["readiness"] = reopened.get_project_readiness(project.key, confirmed_parameters=confirmed)
            report["readiness_without_comparison"] = reopened.get_project_readiness(project.key)["rnaseq.deg"]
            context = reopened.build_execution_context(project.key, confirmed_parameters=confirmed)
            report["context"] = context.model_dump(mode="json")
            report["checks"] = {
                "sources_unchanged": all(item["source_unchanged"] for item in report["imports"].values()),
                "pca_ready": report["readiness"]["rnaseq.pca"]["ready"],
                "deg_ready_with_explicit_comparison": report["readiness"]["rnaseq.deg"]["ready"],
                "downstream_dependencies_not_fabricated": all(not report["readiness"][name]["ready"] for name in
                                                             ("rnaseq.volcano", "rnaseq.heatmap", "rnaseq.go", "rnaseq.kegg")),
                "no_analysis_output_created": not any((project.root / "results").iterdir()),
                "reopened_context_has_four_roles": all(getattr(reopened.manager.get_project_context(project.key), role).is_file() for role in sources),
            }
            report["status"] = "succeeded" if all(report["checks"].values()) else "failed"
        except (ProjectError, OSError, ValueError, KeyError) as error:
            logger.error("Real import test failed: type=%s code=%s", type(error).__name__, getattr(error, "code", "IMPORT_TEST_ERROR"))
            report["error_code"] = getattr(error, "code", "IMPORT_TEST_ERROR")
        finally:
            report["seconds"] = round(time.perf_counter() - started, 3)
            write_json(project.root / "reports/file_import_test.json", report)
            logger.info("Real import test finished: status=%s seconds=%s report=reports/file_import_test.json", report["status"], report["seconds"])
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
