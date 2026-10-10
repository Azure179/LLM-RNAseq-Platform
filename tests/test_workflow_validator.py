"""Deterministic local gate; fixtures are declarations, never analysis results."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import ChatResponse, DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.workflow import HistoricalRun, PlanValidator, WorkflowContext, WorkflowPlan, PlanValidationResult
from bulk_rnaseq.workflow.planner import WorkflowPlanner
from bulk_rnaseq.workspace.models import ProjectContext


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Phase 7.3 cannot invoke API, executors, or R/subprocesses")
    for target, name in [(DeepSeekClient, "_request"), (SkillRegistry, "execute"),
                         (SkillRegistry, "get_executor"), (subprocess, "run"), (subprocess, "Popen")]:
        monkeypatch.setattr(target, name, forbidden)


@pytest.fixture
def validator():
    return PlanValidator(create_default_registry())


@pytest.fixture
def context():
    return WorkflowContext("demo", frozenset({"count_matrix", "metadata", "go_mapping", "kegg_mapping"}),
                           {"rnaseq.pca": {"group_column": "condition"}, "rnaseq.heatmap": {"group_column": "condition"}})


def deg():
    return {"step_id": "deg", "skill_name": "rnaseq.deg", "parameters": {"treatment": "OT", "control": "T"}}


def ref(output="deg_csv", step_id="deg", **extra):
    return {"source": "workflow_step", "step_id": step_id, "output": output, **extra}


def volcano():
    return {"step_id": "volcano", "skill_name": "rnaseq.volcano", "input_bindings": {"deg_result_file": ref()}}


def kegg(subset="up"):
    return {"step_id": "kegg", "skill_name": "rnaseq.kegg", "input_bindings": {
        "gene_list": ref("filtered_csv", gene_subset=subset), "analysis_genes": ref()}}


def plan(*steps):
    return WorkflowPlan(project_id="demo", user_request="Local validation fixture", steps=list(steps))


def codes(result):
    assert not result.valid
    assert result.validated_plan is None
    assert result.execution_order == []
    return {error.code for error in result.errors}


@pytest.mark.parametrize("steps,expected", [
    ([deg(), volcano()], {"deg": [], "volcano": ["deg"]}),
    ([deg(), kegg()], {"deg": [], "kegg": ["deg"]}),
    ([volcano(), kegg(), deg()], {"deg": [], "volcano": ["deg"], "kegg": ["deg"]}),
    ([{"step_id": "pca", "skill_name": "rnaseq.pca"}, deg()], {"pca": [], "deg": []}),
])
def test_required_dags(validator, context, steps, expected):
    original = plan(*steps)
    before = original.model_dump()
    result = validator.validate(original, context)
    assert result.valid, result.errors
    assert result.dependencies == expected
    assert result.validated_plan is not None
    assert result.validated_plan.workflow_id == original.workflow_id
    assert original.model_dump() == before
    for child, parents in expected.items():
        for parent in parents:
            assert result.execution_order.index(parent) < result.execution_order.index(child)
    for step in result.validated_plan.steps:
        assert step.depends_on == expected[step.step_id]
        assert step.input_bindings == result.resolved_bindings[step.step_id]
        assert step.parameters == result.resolved_parameters[step.step_id]


@pytest.mark.parametrize("subset", ["all", "up", "down"])
def test_subset_preserved_not_materialized(validator, context, subset):
    result = validator.validate(plan(deg(), kegg(subset)), context)
    assert result.valid
    step = result.validated_plan.steps[1]
    assert step.input_bindings["gene_list"].gene_subset == subset
    assert "gene_subset" not in step.parameters


def test_defaults_resolved_and_roundtrip(validator, context):
    result = validator.validate(plan(deg()), context)
    assert result.validated_plan.steps[0].parameters["alpha"] == .05
    assert result.validated_plan.steps[0].parameters["lfc_threshold"] == 1
    assert result.validated_plan.steps[0].input_bindings["metadata_path"].key == "metadata"
    assert PlanValidationResult.model_validate_json(result.model_dump_json()) == result
    assert validator.validate(result.validated_plan, context).validated_plan == result.validated_plan


def test_result_is_detached_from_source(validator, context):
    original = plan(deg())
    result = validator.validate(original, context)
    original.steps[0].parameters["treatment"] = "changed"
    assert result.validated_plan.steps[0].parameters["treatment"] == "OT"


@pytest.mark.parametrize("name,code", [("rnaseq.unknown", "UNKNOWN_SKILL"), ("rnaseq.gsea", "SKILL_UNAVAILABLE")])
def test_unknown_unavailable(validator, context, name, code):
    assert code in codes(validator.validate(plan({"step_id": "x", "skill_name": name}), context))


def test_missing_business_parameters_has_field_names(validator, context):
    step = deg()
    step["parameters"] = {}
    result = validator.validate(plan(step), context)
    assert "INVALID_WORKFLOW_PARAMETERS" in codes(result)
    problem = next(error for error in result.errors if error.code == "INVALID_WORKFLOW_PARAMETERS")
    assert set(problem.details["missing"]) == {"treatment", "control"}


@pytest.mark.parametrize("group", ["OT", "NA"])
def test_existing_deg_parameter_validator(validator, context, group):
    step = deg()
    step["parameters"]["control"] = group
    code = "IDENTICAL_GROUPS" if group == "OT" else "INVALID_PARAMETER"
    assert code in codes(validator.validate(plan(step), context))
    # The lower-level public resolver is also fixed, not just the Planner path.
    assert code in codes(validator.resolver.validate(plan(step), context))


def test_float_integer_rejected_not_coerced(validator, context):
    step = volcano()
    step["parameters"] = {"top_n_labels": 2.0}
    result = validator.validate(plan(deg(), step), context)
    assert "INVALID_WORKFLOW_PARAMETERS" in codes(result)
    assert result.errors[0].details["integer_fields"] == ["top_n_labels"]


def test_missing_metadata_role(validator, context):
    result = validator.validate(plan(deg()), replace(context, available_inputs=frozenset({"count_matrix"})))
    assert "MISSING_PROJECT_INPUT" in codes(result)
    assert result.errors[0].details["missing"] == ["metadata"]


def test_missing_upstream_not_added(validator, context):
    step = volcano()
    step["input_bindings"] = {}
    original = plan(step)
    result = validator.validate(original, context)
    assert "MISSING_UPSTREAM_DEPENDENCY" in codes(result)
    assert len(original.steps) == 1


def test_invalid_output(validator, context):
    step = volcano()
    step["input_bindings"]["deg_result_file"]["output"] = "nonexistent_output"
    assert "INVALID_OUTPUT_BINDING" in codes(validator.validate(plan(deg(), step), context))


def test_invalid_chain_not_repaired(validator, context):
    step = kegg()
    step["depends_on"] = ["volcano"]
    result = validator.validate(plan(deg(), volcano(), step), context)
    assert "INVALID_EXECUTION_DEPENDENCY" in codes(result)


def test_cycle(validator, context):
    step = deg()
    step["depends_on"] = ["volcano"]
    assert "WORKFLOW_CYCLE_DETECTED" in codes(validator.validate(plan(step, volcano()), context))


def test_same_type_wrong_artifact_rejected(validator, context):
    step = volcano()
    step["input_bindings"]["deg_result_file"]["output"] = "filtered_csv"
    assert "INCOMPATIBLE_OUTPUT_BINDING" in codes(validator.validate(plan(deg(), step), context))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "demo"
    inputs = root / "input"
    inputs.mkdir(parents=True)
    counts = inputs / "counts.csv"
    metadata = inputs / "metadata.csv"
    counts.write_text("schema-only fixture", encoding="utf-8")
    metadata.write_text("schema-only fixture", encoding="utf-8")
    return ProjectContext("demo", "demo", root, inputs, root / "results", root / "logs", root / "reports",
                          root / "runtime", counts, metadata, None, None)


def test_real_file_context_scope_no_paths_in_result(validator, project):
    result = validator.validate_project(plan(deg()), project)
    assert result.valid
    assert result.scope == "project_files_and_dependency_contract"
    assert str(project.project_root) not in result.model_dump_json()
    assert not project.output_root.exists()


def test_file_removed_after_snapshot_rechecked(validator, project):
    context = WorkflowContext.from_project_context(project)
    assert "metadata" in context.available_inputs
    project.metadata.unlink()  # Only removes this test's temporary fixture.
    result = validator.validate(plan(deg()), context)
    assert "MISSING_PROJECT_INPUT" in codes(result)
    assert "metadata" in result.errors[0].details["missing"]


def test_added_binding_detected_from_fresh_context(validator, project):
    assert "MISSING_PROJECT_INPUT" in codes(validator.validate_project(plan(deg()), replace(project, metadata=None)))
    assert validator.validate_project(plan(deg()), project).valid


def test_unreadable_file(validator, project, monkeypatch):
    original = Path.open
    def open_file(path, *args, **kwargs):
        if path == project.metadata:
            raise PermissionError("private location")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    result = validator.validate_project(plan(deg()), project)
    assert "MISSING_PROJECT_INPUT" in codes(result)
    assert "private location" not in result.model_dump_json()


def test_outside_project_rejected(validator, project, tmp_path):
    other = tmp_path / "private.csv"
    other.write_text("fixture", encoding="utf-8")
    result = validator.validate_project(plan(deg()), replace(project, metadata=other))
    assert "MISSING_PROJECT_INPUT" in codes(result)


def test_external_input_root_not_trusted(validator, project, tmp_path):
    result = validator.validate_project(plan(deg()), replace(project, input_root=tmp_path))
    assert "MISSING_PROJECT_INPUT" in codes(result)


def test_project_identity_mismatch_no_file_reads(validator, project, monkeypatch):
    context = replace(WorkflowContext.from_project_context(project), project_id="other")
    monkeypatch.setattr(Path, "open", Mock(side_effect=AssertionError("Must not read another project")))
    assert "PROJECT_CONTEXT_MISMATCH" in codes(validator.validate(plan(deg()), context))


def test_explicit_history_interface(validator, context):
    lookup = Mock()
    lookup.lookup.return_value = HistoricalRun("demo", "existing-run", "rnaseq.deg", "succeeded", frozenset({"deg_csv"}))
    step = volcano()
    step["input_bindings"]["deg_result_file"] = {"source": "project_result", "run_id": "existing-run", "output": "deg_csv"}
    result = validator.validate(plan(step), replace(context, history=lookup))
    assert result.valid
    assert result.dependencies == {"volcano": []}
    assert result.validated_plan.steps[0].input_bindings["deg_result_file"].run_id == "existing-run"
    lookup.lookup.assert_called_once_with("demo", "existing-run")


@pytest.mark.parametrize("changes", [{"project_id": "other"}, {"run_id": "wrong"}, {"status": "failed"}, {"outputs": frozenset()}])
def test_bad_history_rejected(validator, context, changes):
    provider = Mock()
    run = HistoricalRun("demo", "existing-run", "rnaseq.deg", "succeeded", frozenset({"deg_csv"}))
    provider.lookup.return_value = replace(run, **changes)
    step = volcano()
    step["input_bindings"]["deg_result_file"] = {"source": "project_result", "run_id": "existing-run", "output": "deg_csv"}
    assert codes(validator.validate(plan(step), replace(context, history=provider))) & {
        "MISSING_UPSTREAM_DEPENDENCY", "INVALID_OUTPUT_BINDING"}


@pytest.mark.parametrize("confirmed", [None, [], {"rnaseq.deg": 42}, {"rnaseq.deg": {"alpha": float("nan")}}])
def test_malformed_context_structured_error(validator, context, confirmed):
    assert "INVALID_WORKFLOW_CONTEXT" in codes(validator.validate(plan(deg()), replace(context, confirmed_parameters=confirmed)))


def test_missing_context_safe(validator):
    assert "INVALID_WORKFLOW_CONTEXT" in codes(validator.validate(plan(deg()), None))


def test_invalid_plan_safe(validator, context):
    assert "INVALID_WORKFLOW_PLAN" in codes(validator.validate({"steps": []}, context))


def test_planner_uses_public_validator_without_api(context, monkeypatch):
    planner = WorkflowPlanner(DeepSeekClient(DeepSeekConfig(api_key="fixture-key")), create_default_registry())
    spy = Mock(wraps=planner.validator.validate)
    monkeypatch.setattr(planner.validator, "validate", spy)
    response = ChatResponse("fixture-response", "fixture-model", json.dumps({"status": "ready", "steps": [deg(), volcano()]}), "stop", None, 0)
    result = planner._parse(response, "以 T 为对照比较 OT", context, ["OT", "T"], "fixture-hash")
    spy.assert_called_once()
    assert result.validation.validated_plan is not None
    assert result.plan.steps[0].parameters == {"treatment": "OT", "control": "T"}


def test_planner_context_retains_host_project_but_hides_it(project):
    planner = WorkflowPlanner(DeepSeekClient(DeepSeekConfig(api_key="fixture-key")), create_default_registry())
    context, public = planner._context(WorkflowContext.from_project_context(project), ["OT", "T"])
    assert context.project is project
    assert str(project.project_root) not in json.dumps(public)
