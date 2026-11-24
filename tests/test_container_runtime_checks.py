"""Test the runtime verifier itself; never imply a container has passed."""

import csv
from pathlib import Path

from container.runtime_checks import csv_records, result_snapshot
from test_workflow_executor import host, fake_dispatch, executor, validate, deg, volcano


def test_csv_large_field_restores_process_limit(tmp_path: Path) -> None:
    target = tmp_path / "fixture.csv"
    value = "a" * 150_000
    target.write_text("gene_id,description\ng1," + value + "\n", encoding="utf-8")
    previous = csv.field_size_limit()
    assert list(csv_records(target))[0]["description"] == value
    assert csv.field_size_limit() == previous


def test_snapshot_detects_changed_content_and_retains_lineage(host, fake_dispatch) -> None:
    workflow = executor(host).execute(validate(host, deg(), volcano()))
    source = workflow.step_results["deg"]["run_id"]
    child = workflow.step_results["a_volcano"]["run_id"]
    before = result_snapshot(host[0], "demo")
    assert before[child]["parents"] == [source]
    assert before[source]["workflow_id"] == workflow.workflow_id
    Path(workflow.step_results["deg"]["outputs"]["deg_csv"]).write_text("changed synthetic fixture")
    assert result_snapshot(host[0], "demo")[source]["hashes"] != before[source]["hashes"]


def test_snapshot_detects_missing_artifact(host, fake_dispatch) -> None:
    workflow = executor(host).execute(validate(host, deg()))
    before = result_snapshot(host[0], "demo")
    # This file is created by this test's synthetic executor in pytest's temp dir.
    target = Path(workflow.step_results["deg"]["outputs"]["deg_csv"])
    target.rename(target.with_suffix(".offline"))
    assert result_snapshot(host[0], "demo") != before
