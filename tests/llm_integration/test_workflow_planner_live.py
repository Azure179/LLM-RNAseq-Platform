"""Explicit paid Planner evaluation; real model decisions, zero Skill/R execution."""

from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workflow import WorkflowContext, WorkflowPlan
from bulk_rnaseq.workflow.planner import WorkflowPlanner


CASES = [
    ("01_deg_volcano", "以 T 为对照比较 OT，然后画火山图。", False, "ready", ["deg", "volcano"], None),
    ("02_deg_kegg_up_confirmed", "比较 OT 和 T，并对上调基因做 KEGG。", True, "ready", ["deg", "kegg"], "up"),
    ("03_branch_confirmed", "比较 OT 和 T，画火山图，并做 GO 和 KEGG。", True, "ready", ["deg", "volcano", "go", "kegg"], "all"),
    ("04_independent_roots_confirmed", "画 PCA，并比较 OT 和 T。", True, "ready", ["pca", "deg"], None),
    ("05_missing_comparison", "帮我做差异表达再画火山图。", False, "needs_input", [], None),
    ("06_unsupported", "做单细胞分析。", False, "unsupported", [], None),
    ("07_ambiguous_without_context", "比较 OT 和 T，并对上调基因做 KEGG。", False, "needs_input", [], None),
    ("08_reverse_heatmap_go_down", "以 OT 为对照比较 T，画 Top 30 差异基因热图，并对下调基因做 GO 富集。", False, "ready", ["deg", "heatmap", "go"], "down"),
]


@pytest.fixture(scope="module")
def planner_records(request):
    destination = Path(request.config.getoption("--llm-report-dir")) / datetime.now(timezone.utc).strftime("planner_%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    return destination, []


@pytest.mark.llm
@pytest.mark.parametrize("case_id,text,confirmed_comparison,status,skills,subset", CASES, ids=[case[0] for case in CASES])
def test_live_workflow_planner(case_id, text, confirmed_comparison, status, skills, subset,
                               request, planner_records, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Planner evaluation must never execute any Skill or subprocess")
    for obj, name in [(SkillRegistry, "execute"), (SkillRegistry, "get_executor"),
                      (subprocess, "run"), (subprocess, "Popen")]:
        monkeypatch.setattr(obj, name, forbidden)
    confirmed = {"rnaseq.pca": {"group_column": "condition"}, "rnaseq.heatmap": {"group_column": "condition"}}
    if confirmed_comparison:
        confirmed["rnaseq.deg"] = {"treatment": "OT", "control": "T"}
    # Declared context inventory for planning, not a claim that an analysis ran.
    context = WorkflowContext("planner_evaluation", frozenset({"count_matrix", "metadata", "go_mapping", "kegg_mapping"}), confirmed)
    destination, records = planner_records
    record = {"test_case": case_id, "user_input": text, "confirmed_parameters": confirmed,
              "available_groups": ["T", "OT", "O"], "expected_skills": skills, "expected_status": status,
              "pass": False, "usage": None, "request_count": 0, "skill_execution_status": "not_executed"}
    client = None
    try:
        client = DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file"))))
        planner = WorkflowPlanner(client, create_default_registry())
        record["request_count"] = 1
        result = planner.plan(text, context=context, available_groups=["T", "OT", "O"])
        record.update(model=result.response.model, raw_response=result.response.content,
                      decision=result.decision.model_dump(), plan=result.plan.model_dump() if result.plan else None,
                      validation=result.validation.model_dump() if result.validation else None,
                      usage=result.response.usage, latency_seconds=result.response.latency_seconds,
                      prompt_sha256=result.prompt_sha256, response_id=result.response.response_id)
        assert result.decision.status == status
        if status == "ready":
            assert isinstance(result.plan, WorkflowPlan)
            assert result.validation.valid
            by_skill = {step.skill_name.removeprefix("rnaseq."): step for step in result.plan.steps}
            assert set(by_skill) == set(skills) and len(result.plan.steps) == len(skills)
            deg = by_skill["deg"]
            parameters = result.validation.resolved_parameters[deg.step_id]
            expected_direction = ("T", "OT") if case_id.startswith("08") else ("OT", "T")
            assert (parameters["treatment"], parameters["control"]) == expected_direction
            assert result.validation.dependencies[deg.step_id] == []
            for name, step in by_skill.items():
                if name in {"volcano", "go", "kegg", "heatmap"}:
                    assert result.validation.dependencies[step.step_id] == [deg.step_id]
                if name in {"go", "kegg"}:
                    query = step.input_bindings["gene_list"]
                    assert query.step_id == deg.step_id and query.output == "filtered_csv"
                    assert query.gene_subset == subset
                    background = step.input_bindings["analysis_genes"]
                    assert background.output == "deg_csv" and background.step_id == deg.step_id
                if name == "pca":
                    assert result.validation.dependencies[step.step_id] == []
                if name == "heatmap":
                    assert step.parameters["top_n"] == 30
                    assert "raw_counts" in result.validation.resolved_bindings[step.step_id]
        else:
            assert result.plan is None and result.validation is None
            if status == "needs_input":
                assert {"treatment", "control"} <= set(result.decision.missing_parameters)
        record["pass"] = True
    except DeepSeekError as error:
        record["error"] = error.to_dict()
        if error.response:
            record.update(model=error.response.model, raw_response=error.response.content, usage=error.response.usage,
                          latency_seconds=error.response.latency_seconds, prompt_sha256=error.prompt_sha256)
        pytest.fail(f"{error.code}: {error}", pytrace=False)
    except AssertionError:
        record["assertion_failure"] = "Semantic expectations did not match the actual model response; inspect recorded decision."
        raise
    finally:
        record = client.sanitize_for_audit(record) if client is not None else record
        records.append(record)
        totals = {key: sum((row.get("usage") or {}).get(key, 0) for row in records)
                  for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        write_json(destination / "llm_planner_test_results.json", {
            "cases": records, "n_cases": len(records), "n_passed": sum(row["pass"] for row in records),
            "request_count": sum(row["request_count"] for row in records),
            "usage_totals_available_responses": totals,
            "n_responses_with_usage": sum(row.get("usage") is not None for row in records),
            "scope": "Real DeepSeek planning only; declared context, no biological computation"})
