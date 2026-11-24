"""Explicit Windows -> Linux -> Windows probe in a NEW disposable workspace.

Uses the existing public synthetic demo for a real Windows DESeq2 run; this is
portability validation, not a biological result. Linux reuses that exact run.
"""

import argparse
import json
import logging
from pathlib import Path
import sys

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import FileImportService, ProjectManager
from bulk_rnaseq.workspace.skill_execution import SingleSkillService
from bulk_rnaseq.workspace.result_resolver import ResultResolver
from bulk_rnaseq.workflow.history import ProjectHistory
from runtime_checks import result_snapshot

LOGGER = logging.getLogger("container.roundtrip")
PROJECT = "Windows_Portable_Origin"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare-windows", "reuse-linux", "verify-windows"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--demo", type=Path, default=Path("examples/data"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.mode != "reuse-linux" and sys.platform != "win32":
        raise RuntimeError("This stage must run on the Windows host")
    if args.mode == "reuse-linux" and sys.platform != "linux":
        raise RuntimeError("Reuse stage must run in Linux")
    manager = ProjectManager(args.workspace)
    if args.mode == "prepare-windows":
        snapshot = manager.create_project(PROJECT, description="Public synthetic demo; real DESeq2, cross-OS storage test")
        importer = FileImportService(manager)
        for role, filename in (("count_matrix", "counts.csv"), ("metadata", "metadata.csv")):
            imported = importer.import_file(PROJECT, args.demo / filename, role)
            assert imported.status == "succeeded"
        execution = SingleSkillService(create_default_registry(), manager).execute("rnaseq.deg", PROJECT,
            {"treatment": "treatment", "control": "control"})
        assert execution.result.status == "succeeded", execution.result.to_dict()
        write_json(snapshot.root / "reports/windows_origin.json", {"status": "passed", "source_os": sys.platform,
            "deg_run_id": execution.result.run_id, "snapshot": result_snapshot(manager, PROJECT)})
    else:
        snapshot = manager.open_project(PROJECT)
        context = manager.get_project_context(PROJECT)
        report = context.restore_paths(json.loads((snapshot.root / "reports/windows_origin.json").read_text(encoding="utf-8")))
        before = result_snapshot(manager, PROJECT)
        assert all(before[key] == value for key, value in report["snapshot"].items())
        identity = report["deg_run_id"]
        resolution = ResultResolver(manager, PROJECT).find_compatible_result(skill_name="rnaseq.deg",
            run_id=identity, required_outputs=("deg_csv", "filtered_csv"))
        assert resolution.status == "resolved"
        source, checksum = ProjectHistory(manager, PROJECT).artifact(identity, "deg_csv")
        if args.mode == "reuse-linux":
            execution = SingleSkillService(create_default_registry(), manager).execute("rnaseq.volcano", PROJECT, {}, source_run_id=identity)
            assert execution.result.status == "succeeded", execution.result.to_dict()
            assert sha256(source) == checksum
            write_json(snapshot.root / "reports/linux_reuse.json", {"status": "passed", "source_run_id": identity,
                "snapshot": result_snapshot(manager, PROJECT)})
        else:
            linux = context.restore_paths(json.loads((snapshot.root / "reports/linux_reuse.json").read_text(encoding="utf-8")))
            assert before == linux["snapshot"] and len(before) == len(report["snapshot"]) + 1
            assert sum(row["skill"] == "rnaseq.deg" for row in before.values()) == 1
            write_json(snapshot.root / "reports/windows_return.json", {"status": "passed", "restored_runs": len(before)})
    LOGGER.info("%s passed", args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
