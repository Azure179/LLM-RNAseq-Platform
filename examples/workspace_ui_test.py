"""Real files through Streamlit AppTest (NOT a manual browser acceptance test).

No R or DeepSeek. Existing projects/sources are never overwritten.
Run: python examples/workspace_ui_test.py --project-name Real_DATA_UI_TEST_653
"""

import argparse
import logging
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

from app.state import STATE_KEY, widget_key
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import ProjectManager


def find(elements, label):
    return next(item for item in elements if item.label == label)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default="Real_DATA_UI_TEST_653")
    options = parser.parse_args()
    manager = ProjectManager(ROOT / "workspace")
    if any(p.key == options.project_name for p in manager.list_projects()):
        logging.error("Project already exists; use a new --project-name. Nothing overwritten.")
        return 1
    # Test-only host configuration, no API environment is read or modified.
    import os
    os.environ["RNASEQ_WORKSPACE_ROOT"] = str(manager.root)
    report = {"phase": "6.5.3", "mode": "Streamlit AppTest, not a browser/file-dialog test",
              "status": "failed", "manual_browser_acceptance": "blocked: computer-use cannot reliably determine Windows browser URL",
              "project": options.project_name, "imports": {}, "r_calls": 0, "deepseek_calls": 0}
    started = time.perf_counter()
    at = AppTest.from_file(ROOT / "app/main.py", default_timeout=120).run()
    try:
        find(at.text_input, "Project Name").set_value(options.project_name)
        find(at.text_area, "Description").set_value("Phase 6.5.3 real Streamlit AppTest uploads; manual browser acceptance pending.")
        find(at.button, "Create").click().run()
        assert not at.exception and not at.error, "Project creation UI error"
        state = at.session_state[STATE_KEY]
        assert state.current_project.key == options.project_name
        at.session_state[widget_key(state, "tabs")] = "Files"
        at.run()
        prepared = ROOT / "Real_DATA_TEST/input/prepared_asc"
        sources = [("count_matrix", "Count Matrix", "count_matrix.csv"),
                   ("metadata", "Metadata", "metadata.normalized.csv"),
                   ("go_mapping", "GO Mapping", "gene_to_go.tsv"),
                   ("kegg_mapping", "KEGG Mapping", "gene_to_kegg.tsv")]
        for role, label, filename in sources:
            path = prepared / filename
            before = sha256(path)
            step = time.perf_counter()
            logging.info("AppTest upload started: %s", role)
            find(at.file_uploader, f"{label} file").set_value((filename, path.read_bytes(), "application/octet-stream")).run()
            find(at.button, f"Import {label}").click().run()
            assert not at.exception and not at.error, f"{role} UI error"
            info = at.session_state[STATE_KEY].file_status[role]
            assert info["status"] in {"valid", "warning"}
            report["imports"][role] = {"summary": info["summary"], "validation": info["validation"],
                                       "warnings": info["warnings"], "status": info["status"],
                                       "source": path.relative_to(ROOT).as_posix(), "source_sha256": before,
                                       "source_unchanged": sha256(path) == before,
                                       "seconds": round(time.perf_counter() - step, 3)}
            logging.info("AppTest upload completed: %s", role)
        report["readiness"] = at.session_state[STATE_KEY].project_readiness
        assert report["readiness"]["rnaseq.pca"]["ready"]
        assert report["readiness"]["rnaseq.deg"]["data_ready"]
        assert set(report["readiness"]["rnaseq.deg"]["missing"]) == {"treatment", "control"}
        assert all(item["source_unchanged"] for item in report["imports"].values())
        # A new Streamlit session restores the project from disk, not old state.
        reopened = AppTest.from_file(ROOT / "app/main.py", default_timeout=120).run()
        find(reopened.selectbox, "Projects").select(options.project_name).run()
        assert not reopened.exception and not reopened.error
        assert reopened.session_state[STATE_KEY].file_status["count_matrix"]["summary"]["n_genes"] == report["imports"]["count_matrix"]["summary"]["n_genes"]
        report["new_session_restore"] = True
        report["results_empty"] = not any((manager.root / options.project_name / "results").iterdir())
        report["status"] = "succeeded"
    except Exception as error:
        logging.error("AppTest real test failed: %s", type(error).__name__)
        report["error_type"] = type(error).__name__
    report["seconds"] = round(time.perf_counter() - started, 3)
    project_root = manager.root / options.project_name
    if project_root.is_dir():
        write_json(project_root / "reports/streamlit_apptest.json", report)
    logging.info("AppTest real test status=%s seconds=%s", report["status"], report["seconds"])
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
