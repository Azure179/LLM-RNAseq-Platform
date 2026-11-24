"""Final demo step. Run only inside the disposable public-demo container."""
import csv
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import httpx
from bulk_rnaseq.workspace import FileImportService, ProjectManager


def main() -> None:
    project = "PublicDemo"
    folder = Path("/tmp/public-demo")
    manager = ProjectManager(Path("/workspace"))
    manager.update_project_metadata(project, {"description": "Public synthetic RNA-seq demonstration, 30 genes and six samples."})
    root = manager.open_project(project).root
    report = json.loads((root / "reports/container_smoke.json").read_bytes())
    restart = json.loads((root / "reports/container_restart.json").read_bytes())
    old = report["deg_run_id"]
    assert restart["status"] == "passed" and restart["reused_deg_run_id"] == old
    workflow = report["workflow"]["step_results"]
    assert report["customization"]["source_run_id"] == old
    with TemporaryDirectory() as temp:
        path = Path(temp) / "counts.csv"
        with (folder / path.name).open(newline="") as stream:
            rows = list(csv.reader(stream))
        rows[1][1] = str(int(rows[1][1]) + 1)
        with path.open("w", newline="") as stream:
            csv.writer(stream, lineterminator="\n").writerows(rows)
        binding = FileImportService(manager).replace_file(project, path, "count_matrix")
        assert binding.status == "succeeded"
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=600) as client:
        def run(skill: str, parameters: dict, **extra: str) -> httpx.Response:
            return client.post(f"/skills/rnaseq.{skill}/run",
                               json={"project_id": project, "parameters": parameters, **extra})

        rejected = run("volcano", {})
        assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "STALE_DEG_RESULT"
        history = run("volcano", {}, source_run_id=old).raise_for_status().json()
        new = run("deg", {"treatment": "treatment", "control": "control"}).raise_for_status().json()
        current = run("volcano", {}).raise_for_status().json()
        for result, parent, scope in ((history, old, "historical_run"),
                                      (current, new["run_id"], "current_input_snapshot")):
            assert result["status"] == "succeeded" and result["metadata"]["parent_run_ids"] == [parent]
            assert result["metadata"]["upstream_sources"]["deg_result_file"]["reuse_scope"] == scope
        assert new["status"] == "succeeded" and new["run_id"] != old
    shutil.copyfile(root / workflow["volcano"]["outputs"]["plot"], folder / "volcano.png")
    summary = {"dataset": "public synthetic fixture", "comparison": "treatment vs control",
               "deg_summary": workflow["deg"]["summary"], "original_deg": old, "replacement_deg": new["run_id"],
               "historical_volcano": history["run_id"], "current_volcano": current["run_id"],
               "stale_request": rejected.json()["error"]["code"], "customization_parent": old,
               "enrichment": report["enrichment_checks"]}
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
