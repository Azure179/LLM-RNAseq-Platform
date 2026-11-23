"""Permanent offline lineage regressions; mocks are explicitly not biological evidence."""

from datetime import datetime, timezone
from uuid import uuid4
import json
import pytest

from test_workflow_chat_ui import host, run_workflow
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.result_ref import ResultRef
from bulk_rnaseq.workspace.result_resolver import ResultResolver
from bulk_rnaseq.workspace.chat_controller import ChatController
from bulk_rnaseq.workspace.chat_store import ChatStore


def resolve(controller, **kwargs):
    return ResultResolver(controller.manager, "First").find_compatible_result(
        skill_name="rnaseq.deg", required_outputs=("deg_csv", "filtered_csv"), **kwargs)


@pytest.mark.parametrize("skill,subset", [("rnaseq.volcano", "all"), ("rnaseq.kegg", "up"),
                                        ("rnaseq.go", "down"), ("rnaseq.kegg", "all")])
def test_followup_and_restart_real_binding_no_deg_rerun(host, skill, subset):
    controller, answers, requests, calls, _ = host
    initial = run_workflow(host)
    deg = next(s["run_id"] for s in initial.steps if s["skill_name"] == "rnaseq.deg")
    before = len(calls)
    # New controller and new ResultManager, without session state/cache.
    controller = ChatController(controller.manager, controller.manager.root / "unused.env",
                                runner_factory=controller.runner_factory)
    answers.append({"request_type": "result_reuse", "target_skill": skill,
        "source_reference": "recent", "gene_subset": subset})
    result = controller.submit("First", "使用刚才DEG的结果", str(uuid4()))
    assert result.status == "succeeded", result
    assert result.source_run_id == deg
    assert calls[before:] == [skill]
    ref = next(r for r in ResultManager(controller.manager, "First").list_result_refs() if r.run_id == result.run_id)
    assert ref.parent_run_ids == [deg]
    root = controller.manager.open_project("First").root / "results/workflows" / result.workflow_id
    record = json.loads((root / "steps/001/provenance.json").read_text())
    for source in record["upstream_sources"].values():
        if source["source"] == "project_result":
            assert source["source_run_id"] == deg and source["source_skill"] == "rnaseq.deg"
    if skill in {"rnaseq.go", "rnaseq.kegg"}:
        import csv
        source = record["upstream_sources"]["gene_list"]
        assert source["gene_subset"] == subset
        from pathlib import Path
        with controller.manager.get_project_context("First").resolve_path(source["derived_path"]).open() as handle:
            ids = {r["gene_id"] for r in csv.DictReader(handle)}
        assert ids == ({"g1"} if subset == "up" else {"g2"} if subset == "down" else {"g1", "g2"})
    assert controller.submit("First", result.user_message, result.request_id) == result
    assert calls[before:] == [skill]


def test_ambiguous_then_explicit_choice_no_automatic_latest(host):
    controller, answers, requests, calls, _ = host
    run_workflow(host)
    run_workflow(host)
    before = len(calls)
    answers.append({"request_type": "result_reuse", "target_skill": "rnaseq.volcano",
        "treatment": "OT", "control": "T"})
    result = controller.submit("First", "使用OT vs T结果画火山图", str(uuid4()))
    assert result.status == "needs_input" and len(result.source_options) == 2
    assert len(calls) == before
    chosen = result.source_options[1]["run_id"]
    result = controller.submit("First", result.user_message, result.request_id, source_run_id=chosen)
    assert result.status == "succeeded" and result.source_run_id == chosen and result.error_code is None
    assert calls[before:] == ["rnaseq.volcano"]


def test_missing_and_isolation(host):
    controller, answers, _, calls, _ = host
    answers.append({"request_type": "result_reuse", "target_skill": "rnaseq.volcano"})
    result = controller.submit("First", "画火山图", str(uuid4()))
    assert result.error_code == "MISSING_UPSTREAM_DEPENDENCY" and not calls
    initial = run_workflow(host)
    deg = next(s["run_id"] for s in initial.steps if s["skill_name"] == "rnaseq.deg")
    other = ResultResolver(controller.manager, "Second").find_compatible_result(skill_name="rnaseq.deg", run_id=deg)
    assert other.status == "not_found"
    assert resolve(controller, treatment="T", control="OT").status == "not_found"
    assert resolve(controller, treatment="OT", control="T").result.run_id == deg


def test_recent_uses_chat_not_external_newer_run(host):
    controller, _, _, _, _ = host
    first = run_workflow(host)
    store = ChatStore(controller.manager, "First")
    chat_before = store.history()
    newer = run_workflow(host)
    # Synthetic external execution: remove only fixture chat attribution, retain its real fixture result records.
    from unittest.mock import patch
    with patch.object(ChatStore, "history", return_value=chat_before):
        selected = resolve(controller, selection="recent")
    expected = next(s["run_id"] for s in first.steps if s["skill_name"] == "rnaseq.deg")
    assert selected.result.run_id == expected
    assert resolve(controller, selection="latest").result.run_id != expected


def test_datetime_order_not_uuid_strings_or_timezone_strings(host, monkeypatch):
    controller, _, _, _, _ = host
    for _ in range(3):
        run_workflow(host)
    browser = ResultManager(controller.manager, "First")
    smallest, middle, largest = sorted(browser.list_result_refs(skill_name="rnaseq.deg"), key=lambda r: r.run_id)
    refs = [ref.model_copy(update={"created_at": datetime.fromisoformat(stamp), "completed_at": None})
            for ref, stamp in [(largest, "2026-10-10T09:00:00+08:00"),  # UTC 01:00, oldest
                               (smallest, "2026-10-10T02:00:00+00:00"),  # UTC 02:00, newest
                               (middle, "2026-10-10T01:30:00+00:00")]]
    monkeypatch.setattr(ResultManager, "list_result_refs", lambda self, **kwargs: refs)
    assert resolve(controller, selection="latest").result.run_id == smallest.run_id
    assert resolve(controller).status == "ambiguous"
    assert resolve(controller, run_id=largest.run_id).result.run_id == largest.run_id
    # Newer unrelated comparison must never override the requested direction.
    refs.append(refs[1].model_copy(update={"run_id": "new_unrelated", "treatment": "O",
        "created_at": datetime(2026, 10, 11, tzinfo=timezone.utc)}))
    assert resolve(controller, selection="latest", treatment="OT", control="T").result.run_id == smallest.run_id


def test_no_vst_or_fake_up_down_files_are_advertised(host):
    controller, _, _, _, _ = host
    run_workflow(host)
    ref = resolve(controller).result
    assert "deg_csv" in ref.outputs and "filtered_csv" in ref.outputs
    assert not {"vst", "deg_up", "deg_down"} & ref.outputs.keys()
    assert ref.workflow_id and ref.step_id
    folder = controller.manager.open_project("First").root / "results/result_index"
    assert len(list(folder.glob("*.json"))) == 3
    for child in ResultManager(controller.manager, "First").list_result_refs():
        assert (child.treatment, child.control) == ("OT", "T")


def test_changed_artifact_rejected_and_source_unchanged_on_bad_parameters(host):
    controller, answers, _, calls, _ = host
    run_workflow(host)
    before = list(calls)
    answers.append({"request_type": "result_reuse", "target_skill": "rnaseq.volcano",
                   "parameters": {"padj_threshold": 2}})
    result = controller.submit("First", "火山图 padj 2", str(uuid4()))
    assert result.error_code == "INVALID_WORKFLOW_PARAMETERS" and calls == before
    ref = resolve(controller).result
    root = controller.manager.open_project("First").root / "results"
    # Only this test's synthetic fixture output is altered.
    (root / ref.outputs["deg_csv"]).write_text("tampered fixture")
    assert resolve(controller).status == "not_found"


def test_indexed_step_survives_before_workflow_final_document(host):
    controller, _, _, _, _ = host
    result = run_workflow(host)
    root = controller.manager.open_project("First").root / "results"
    # Simulate an interrupted final aggregation using temporary test data only.
    (root / "workflows" / result.workflow_id / "workflow_result.json").unlink()
    selected = resolve(controller)
    assert selected.status == "resolved" and selected.result.workflow_id == result.workflow_id


def test_recent_inherits_deg_parent_of_last_followup(host):
    controller, answers, _, _, _ = host
    original = run_workflow(host)
    deg = next(s["run_id"] for s in original.steps if s["skill_name"] == "rnaseq.deg")
    for skill in ("rnaseq.volcano", "rnaseq.kegg"):
        answers.append({"request_type": "result_reuse", "target_skill": skill, "source_reference": "recent"})
        result = controller.submit("First", "使用刚才的DEG", str(uuid4()))
        assert result.status == "succeeded" and result.source_run_id == deg


def test_changed_recent_or_latest_never_silently_uses_older_deg(host):
    controller, _, _, _, _ = host
    run_workflow(host)
    run_workflow(host)
    selected = resolve(controller, selection="recent").result
    root = controller.manager.open_project("First").root / "results"
    (root / selected.outputs["deg_csv"]).write_text("tampered fixture")
    for choice in ("recent", "latest"):
        assert resolve(controller, selection=choice).status == "not_found"
