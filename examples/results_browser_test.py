"""Read-only real result AppTest; optional byte-identical Phase 4 result copies.

No DeepSeek, R, Skill execution or synthetic analysis results.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import sys
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from app.state import STATE_KEY, set_current_project, widget_key
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import FileImportService, ProjectManager
from bulk_rnaseq.workspace.result_manager import ResultManager


SOURCES = {"volcano": "Real_DATA_TEST/results/03_volcano", "heatmap": "Real_DATA_TEST/results/04_heatmap",
           "go": "Real_DATA_TEST/results/asc_mapped/05_go_retry", "kegg": "Real_DATA_TEST/results/asc_mapped/06_kegg",
           "failed_go": "Real_DATA_TEST/results/05_go"}


def copy_existing(project_root: Path) -> list[dict]:
    records = []
    for name, relative in SOURCES.items():
        source = (ROOT / relative).resolve()
        target = (project_root / "results" / "phase_6_5_5_existing" / name).resolve()
        if not source.is_relative_to(ROOT) or not target.is_relative_to(project_root / "results"):
            raise ValueError("Copy paths escaped expected roots")
        for item in source.rglob("*"):
            if item.is_symlink() or item.is_junction():
                raise ValueError("Linked source is unsupported")
        if not target.exists():
            shutil.copytree(source, target)
        # Existing copies are verified, never overwritten to make a test pass.
        for item in source.rglob("*"):
            if item.is_file():
                copied = target / item.relative_to(source)
                digest = sha256(item)
                assert copied.is_file() and sha256(copied) == digest
                records.append({"source": item.relative_to(ROOT).as_posix(),
                                "copy": copied.relative_to(project_root).as_posix(), "sha256": digest})
    return records


def element(elements, label):
    return next(item for item in elements if item.label == label)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="Real_DATA_UI_TEST")
    parser.add_argument("--prepare-existing", action="store_true", help="Copy existing real Phase 4 outputs into project once")
    args = parser.parse_args()
    projects = ProjectManager(ROOT / "workspace")
    project = projects.open_project(args.project)
    report = {"mode": "Streamlit AppTest (not graphical browser acceptance)", "status": "failed",
              "project": args.project, "r_calls": 0, "deepseek_calls": 0, "runs": []}
    if args.prepare_existing:
        report["copied_files"] = copy_existing(project.root)
    directory = project.root / "reports" / ("results_browser_" + str(uuid4()))
    directory.mkdir()
    started = perf_counter()
    try:
        manager = ResultManager(projects, args.project)
        runs = manager.list_runs()
        assert not manager.diagnostics, manager.diagnostics
        os.environ["RNASEQ_WORKSPACE_ROOT"] = str(projects.root)
        at = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=60).run()
        set_current_project(at.session_state[STATE_KEY], FileImportService(projects), args.project)
        at.session_state[STATE_KEY].selected_tab = "Results"
        at.run()
        assert not at.exception
        for name in ("rnaseq.pca", "rnaseq.deg", "rnaseq.volcano", "rnaseq.heatmap", "rnaseq.go", "rnaseq.kegg"):
            run = next(r for r in runs if r.skill_name == name and r.status == "succeeded")
            element(at.selectbox, "Skill").select(name).run()
            element(at.selectbox, "Analysis run").select(run.run_id).run()
            assert not at.exception and not at.error, name
            checked = {"skill": name, "run_id": run.run_id, "summary": run.summary, "artifacts": []}
            for artifact in run.artifacts:
                assert not artifact.error_code, artifact
                path = manager.validate_result_path(artifact.relative_path)
                before = sha256(path)
                if artifact.kind == "IMAGE":
                    manager.preview_image(run.run_id, artifact.artifact_id)
                elif artifact.kind == "TABLE":
                    preview = manager.preview_table(run.run_id, artifact.artifact_id)
                    assert len(preview.frame) <= 100
                elif artifact.kind == "JSON":
                    manager.preview_json(run.run_id, artifact.artifact_id)
                filename, data, _ = manager.download(run.run_id, artifact.artifact_id)
                import hashlib
                assert hashlib.sha256(data).hexdigest() == before
                assert sha256(path) == before
                checked["artifacts"].append({"file": filename, "kind": artifact.kind, "sha256": before, "size": len(data)})
            categories = element(at.radio, "Artifact category").options
            for category in categories:
                element(at.radio, "Artifact category").set_value(category).run()
                assert not at.exception and not at.error, (name, category)
                if category == "Figures":
                    assert at.get("image")
                elif category == "Tables":
                    assert any(len(frame.value) <= 100 for frame in at.dataframe)
                elif category == "JSON / Metadata":
                    assert at.json
                assert at.download_button
            report["runs"].append(checked)
            logging.info("Real result preview passed: %s %s", name, run.run_id)
        element(at.selectbox, "Skill").select("All").run()
        element(at.selectbox, "Status").select("Failed").run()
        assert any("NO_MAPPED_GENES" in error.value for error in at.error)
        report["failed_run_visible"] = True
        # Actual Chat -> canonical Skill run_id linkage, no new chat submission.
        state = at.session_state[STATE_KEY]
        at.session_state[widget_key(state, "tabs")] = "Assistant"
        at.run()
        link = next(button for button in at.button if button.label == "View Results")
        link.click().run()
        assert at.session_state[STATE_KEY].selected_tab == "Results" and not at.exception
        selected = manager.get_run(at.session_state[STATE_KEY].selected_run_id)
        assert selected.triggered_by
        report["chat_link_run_id"] = selected.run_id
        report["status"] = "passed"
    finally:
        report["seconds"] = round(perf_counter() - started, 3)
        write_json(directory / "report.json", report)
        logging.info("Result browser test report: %s", directory / "report.json")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
