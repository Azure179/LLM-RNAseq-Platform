"""Phase 7.1 tests: declarations and graph checks, never fake analysis outputs."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.workflow import DependencyResolver, HistoricalRun, WorkflowContext, WorkflowPlan, WorkflowResult, WorkflowStep
from bulk_rnaseq.workflow.models import PlanValidationResult
from bulk_rnaseq.workspace.models import ProjectContext


@pytest.fixture(autouse=True)
def no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        pytest.fail("Phase 7.1 must not execute Skills, R or DeepSeek")
    for cls, method in [(SkillRegistry, "execute"), (SkillRegistry, "get_executor"),
                        (DeepSeekClient, "_request"), (subprocess, "run"), (subprocess, "Popen")]:
        monkeypatch.setattr(cls, method, blocked)


@pytest.fixture
def resolver() -> DependencyResolver:
    return DependencyResolver(create_default_registry())


@pytest.fixture
def context() -> WorkflowContext:
    return WorkflowContext("demo", frozenset({"count_matrix", "metadata", "go_mapping", "kegg_mapping"}),
                           {"rnaseq.pca": {"group_column": "condition"},
                            "rnaseq.heatmap": {"group_column": "condition"}})


def plan(*steps: dict) -> dict:
    return {"project_id": "demo", "user_request": "Deterministic contract test only", "steps": list(steps)}


def deg(**kwargs: object) -> dict:
    return {"step_id": "deg", "skill_name": "rnaseq.deg", "parameters": {"treatment": "OT", "control": "T"}, **kwargs}


def ref(output: str = "deg_csv", step: str = "deg", **kwargs: object) -> dict:
    return {"source": "workflow_step", "step_id": step, "output": output, **kwargs}


def volcano(**kwargs: object) -> dict:
    return {"step_id": "volcano", "skill_name": "rnaseq.volcano", "input_bindings": {"deg_result_file": ref()}, **kwargs}


def enrichment(skill: str = "kegg", subset: str = "up") -> dict:
    return {"step_id": skill, "skill_name": "rnaseq." + skill,
            "input_bindings": {"gene_list": ref("filtered_csv", gene_subset=subset), "analysis_genes": ref()}}


def codes(result: PlanValidationResult) -> set[str]:
    assert not result.valid
    assert result.execution_order == []
    return {error.code for error in result.errors}


@pytest.mark.parametrize("step", [deg(), {"step_id": "pca", "skill_name": "rnaseq.pca"}])
def test_single_root(resolver, context, step):
    result = resolver.validate(plan(step), context)
    assert result.valid, result.errors
    assert result.dependencies == {step["step_id"]: []}
    assert set(result.resolved_bindings[step["step_id"]]) in ({"count_matrix_path", "metadata_path"}, {"count_matrix", "metadata"})


def test_deg_volcano(resolver, context):
    result = resolver.validate(plan(volcano(depends_on=["deg"]), deg()), context)
    assert result.valid, result.errors
    assert result.execution_order == ["deg", "volcano"]
    assert result.resolved_bindings["volcano"]["deg_result_file"].output == "deg_csv"


@pytest.mark.parametrize("skill", ["go", "kegg"])
@pytest.mark.parametrize("subset", ["all", "up", "down"])
def test_deg_enrichment_subsets(resolver, context, skill, subset):
    result = resolver.validate(plan(enrichment(skill, subset), deg()), context)
    assert result.valid, result.errors
    assert result.execution_order == ["deg", skill]
    assert result.resolved_bindings[skill]["gene_list"].gene_subset == subset
    assert result.resolved_bindings[skill]["analysis_genes"].output == "deg_csv"
    assert "gene_subset" not in result.resolved_parameters[skill]


def test_branch_not_chain(resolver, context):
    result = resolver.validate(plan(volcano(), enrichment(), deg()), context)
    assert result.valid, result.errors
    assert result.dependencies == {"volcano": ["deg"], "kegg": ["deg"], "deg": []}
    # Tie-break is lexical, not an invented Volcano -> KEGG dependency.
    assert result.execution_order == ["deg", "kegg", "volcano"]


def test_stable_topology_independent_of_plan_list_order(resolver, context):
    first = resolver.validate(plan(deg(), volcano(), enrichment()), context)
    second = resolver.validate(plan(enrichment(), volcano(), deg()), context)
    assert first.valid and second.valid
    assert first.execution_order == second.execution_order
    assert first.dependencies == second.dependencies


def test_manifest_project_roles_agree_with_existing_workspace_bridge(resolver):
    from bulk_rnaseq.workspace.readiness import PARAMETER_ROLES
    for meta in resolver.contracts.metadata.values():
        for parameter, role in meta.project_inputs.items():
            if parameter in PARAMETER_ROLES:
                assert role == PARAMETER_ROLES[parameter].value


def test_pca_deg_independent(resolver, context):
    result = resolver.validate(plan({"step_id": "pca", "skill_name": "rnaseq.pca"}, deg()), context)
    assert result.valid
    assert result.dependencies == {"pca": [], "deg": []}


def test_heatmap_shared_raw_vst_path(resolver, context):
    heat = {"step_id": "heat", "skill_name": "rnaseq.heatmap", "input_bindings": {"deg_result_file": ref()}}
    result = resolver.validate(plan(heat, deg()), context)
    assert result.valid, result.errors
    assert result.resolved_bindings["heat"]["raw_counts"].key == "count_matrix"
    assert "normalized_expression" not in result.resolved_bindings["heat"]
    assert result.dependencies["heat"] == ["deg"]


def test_heatmap_vst_reuse_not_z_scores(resolver, context):
    first = {"step_id": "heat1", "skill_name": "rnaseq.heatmap", "input_bindings": {"deg_result_file": ref()}}
    second = {"step_id": "heat2", "skill_name": "rnaseq.heatmap", "input_bindings": {
        "deg_result_file": ref(), "normalized_expression": ref("normalized_expression", "heat1")}}
    result = resolver.validate(plan(second, deg(), first), context)
    assert result.valid, result.errors
    assert result.execution_order == ["deg", "heat1", "heat2"]
    assert "raw_counts" not in result.resolved_bindings["heat2"]
    second["input_bindings"]["normalized_expression"]["output"] = "matrix"
    assert "INCOMPATIBLE_OUTPUT_BINDING" in codes(resolver.validate(plan(deg(), first, second), context))


@pytest.mark.parametrize("skill,output", [("pca", "coordinates"), ("pca", "normalized_expression"), ("deg", "normalized_expression")])
def test_no_fabricated_vst_outputs(resolver, context, skill, output):
    heat = {"step_id": "heat", "skill_name": "rnaseq.heatmap", "input_bindings": {
        "deg_result_file": ref(), "normalized_expression": ref(output, skill)}}
    result = resolver.validate(plan(deg(), {"step_id": "pca", "skill_name": "rnaseq.pca"}, heat), context)
    assert codes(result) & {"INVALID_OUTPUT_BINDING", "INCOMPATIBLE_OUTPUT_BINDING"}


def test_missing_deg_no_auto_completion(resolver, context):
    result = resolver.validate(plan(volcano(input_bindings={})), context)
    assert "MISSING_UPSTREAM_DEPENDENCY" in codes(result)
    assert set(result.dependencies) == {"volcano"}
    # Merely listing DEG does not authorize an arbitrary association.
    assert "MISSING_UPSTREAM_DEPENDENCY" in codes(resolver.validate(plan(deg(), volcano(input_bindings={})), context))


def test_missing_project_metadata(resolver, context):
    result = resolver.validate(plan(deg()), replace(context, available_inputs=frozenset({"count_matrix"})))
    assert "MISSING_PROJECT_INPUT" in codes(result)
    assert result.errors[0].details["missing"] == ["metadata"]


@pytest.mark.parametrize("skill,code", [("rnaseq.seurat", "UNKNOWN_SKILL"), ("rnaseq.gsea", "SKILL_UNAVAILABLE")])
def test_unknown_unavailable(resolver, context, skill, code):
    assert code in codes(resolver.validate(plan({"step_id": "x", "skill_name": skill}), context))


@pytest.mark.parametrize("steps", [
    [deg(depends_on=["pca"]), {"step_id": "pca", "skill_name": "rnaseq.pca", "depends_on": ["deg"]}],
    [volcano(input_bindings={"deg_result_file": ref("plot", "volcano")})],
])
def test_cycle(resolver, context, steps):
    assert "WORKFLOW_CYCLE_DETECTED" in codes(resolver.validate(plan(*steps), context))


def test_reject_unrelated_ordering_edge(resolver, context):
    assert "INVALID_EXECUTION_DEPENDENCY" in codes(resolver.validate(
        plan(deg(depends_on=["pca"]), {"step_id": "pca", "skill_name": "rnaseq.pca"}), context))


def test_unknown_step(resolver, context):
    assert "UNKNOWN_WORKFLOW_STEP" in codes(resolver.validate(plan(volcano()), context))


@pytest.mark.parametrize("output", ["nonexistent_output", "deg_up", "deg_down"])
def test_invalid_output(resolver, context, output):
    step = enrichment()
    step["input_bindings"]["gene_list"]["output"] = output
    assert "INVALID_OUTPUT_BINDING" in codes(resolver.validate(plan(deg(), step), context))


def test_filtered_deg_cannot_feed_volcano(resolver, context):
    step = volcano(input_bindings={"deg_result_file": ref("filtered_csv")})
    assert "INCOMPATIBLE_OUTPUT_BINDING" in codes(resolver.validate(plan(deg(), step), context))


def test_subset_cannot_filter_background(resolver, context):
    step = enrichment()
    step["input_bindings"]["analysis_genes"]["gene_subset"] = "up"
    assert "INVALID_GENE_SUBSET_BINDING" in codes(resolver.validate(plan(deg(), step), context))


def test_unknown_subset_rejected(resolver, context):
    assert "INVALID_WORKFLOW_PLAN" in codes(resolver.validate(plan(deg(), enrichment(subset="invented")), context))


def test_background_required_no_annotation_fallback(resolver, context):
    step = enrichment()
    del step["input_bindings"]["analysis_genes"]
    assert "MISSING_UPSTREAM_DEPENDENCY" in codes(resolver.validate(plan(deg(), step), context))


def test_alternatives_exactly_one(resolver, context):
    step = enrichment()
    step["input_bindings"]["universe"] = {"source": "project_input", "key": "universe"}
    context = replace(context, available_inputs=context.available_inputs | {"universe"})
    assert "CONFLICTING_INPUT_BINDINGS" in codes(resolver.validate(plan(deg(), step), context))


def test_two_available_backgrounds_require_explicit_selection(resolver, context):
    step = enrichment()
    del step["input_bindings"]["analysis_genes"]
    context = replace(context, available_inputs=context.available_inputs | {"universe", "analysis_genes"})
    assert "AMBIGUOUS_INPUT_BINDING" in codes(resolver.validate(plan(deg(), step), context))


@pytest.mark.parametrize("parameters", [
    {"output_dir": "results/fake"}, {"count_matrix_path": "C:/private/counts.csv"},
    {"rscript_executable": "dangerous"}, {"timeout": 50}, {"api_key": "not-a-secret"},
    {"alpha": 2}, {"lfc_threshold": -1}, {"alpha": True}, {"unexpected": 1},
])
def test_parameters_constraints_and_host_boundary(resolver, context, parameters):
    result = resolver.validate(plan(deg(parameters={"treatment": "OT", "control": "T", **parameters})), context)
    assert "INVALID_WORKFLOW_PARAMETERS" in codes(result)
    assert "C:/private" not in result.model_dump_json()


def test_missing_comparison_not_inferred(resolver, context):
    assert "INVALID_WORKFLOW_PARAMETERS" in codes(resolver.validate(plan(deg(parameters={})), context))


def test_defaults_from_registered_schema(resolver, context):
    result = resolver.validate(plan(deg(), enrichment()), context)
    assert result.valid
    for skill, step_id in [("rnaseq.deg", "deg"), ("rnaseq.kegg", "kegg")]:
        for key, prop in resolver.contracts.schemas[skill]["properties"].items():
            if "default" in prop and prop["default"] is not None:
                assert result.resolved_parameters[step_id][key] == prop["default"]


@pytest.mark.parametrize("binding,code", [
    ({"source": "project_input", "key": "go_mapping"}, "INCOMPATIBLE_INPUT_BINDING"),
    ({"source": "project_input", "key": "deg_result"}, "MISSING_PROJECT_INPUT"),
    ({"source": "project_input", "key": "C:/file.csv"}, "INVALID_WORKFLOW_PLAN"),
])
def test_project_input_role_boundary(resolver, context, binding, code):
    assert code in codes(resolver.validate(plan(volcano(input_bindings={"deg_result_file": binding})), context))


def test_binding_cannot_supply_system_parameter(resolver, context):
    assert "INVALID_INPUT_BINDING" in codes(resolver.validate(plan(deg(input_bindings={
        "output_dir": {"source": "project_input", "key": "metadata"}})), context))


def test_explicit_external_full_result(resolver, context):
    context = replace(context, available_inputs=context.available_inputs | {"deg_result"})
    result = resolver.validate(plan(volcano(input_bindings={})), context)
    assert result.valid
    assert result.dependencies == {"volcano": []}


def history_context(context, **changes):
    run = HistoricalRun("demo", "actual-run-1", "rnaseq.deg", "succeeded", frozenset({"deg_csv", "filtered_csv"}))
    history = Mock()
    history.lookup.return_value = replace(run, **changes)
    return replace(context, history=history)


def historical_volcano():
    return volcano(input_bindings={"deg_result_file": {"source": "project_result", "run_id": "actual-run-1", "output": "deg_csv"}})


def test_history_exact_run_interface(resolver, context):
    context = history_context(context)
    result = resolver.validate(plan(historical_volcano()), context)
    assert result.valid, result.errors
    context.history.lookup.assert_called_once_with("demo", "actual-run-1")
    assert result.dependencies == {"volcano": []}


@pytest.mark.parametrize("changes", [{"project_id": "other"}, {"run_id": "different-run"}, {"status": "failed"}, {"outputs": frozenset()}])
def test_history_isolation_and_missing_artifact(resolver, context, changes):
    result = resolver.validate(plan(historical_volcano()), history_context(context, **changes))
    assert codes(result) & {"MISSING_UPSTREAM_DEPENDENCY", "INVALID_OUTPUT_BINDING"}


def test_history_provider_absent(resolver, context):
    assert "MISSING_UPSTREAM_DEPENDENCY" in codes(resolver.validate(plan(historical_volcano()), context))


def test_history_failure_safe(resolver, context):
    context = history_context(context)
    context.history.lookup.side_effect = OSError("private path must not leak")
    result = resolver.validate(plan(historical_volcano()), context)
    assert "HISTORY_LOOKUP_FAILED" in codes(result)
    assert "private path" not in result.model_dump_json()


def test_new_registration_requires_new_contract_snapshot(resolver, context):
    from bulk_rnaseq.skills import run_deg
    manifest = resolver.registry.get_manifest("rnaseq.deg")
    manifest["name"] = "rnaseq.newdeg"
    manifest["output_schema"]["properties"]["skill_name"]["const"] = "rnaseq.newdeg"
    resolver.registry.register(manifest, run_deg)
    result = resolver.validate(plan(deg(skill_name="rnaseq.newdeg")), context)
    assert "INVALID_WORKFLOW_CONTRACT" in codes(result)


def test_history_project_service_error_safe(resolver, context):
    from bulk_rnaseq.workspace.errors import ProjectError
    context = history_context(context)
    context.history.lookup.side_effect = ProjectError("ARTIFACT_MISSING", "private artifact")
    assert "HISTORY_LOOKUP_FAILED" in codes(resolver.validate(plan(historical_volcano()), context))


def test_plan_project_isolation_no_history_access(resolver, context):
    context = history_context(context)
    result = resolver.validate(plan(historical_volcano()), replace(context, project_id="other"))
    assert "PROJECT_CONTEXT_MISMATCH" in codes(result)
    context.history.lookup.assert_not_called()


@pytest.mark.parametrize("mutation", [
    lambda p: p["steps"].append(deg()),
    lambda p: p["steps"][0].update(step_id="../escape"),
    lambda p: p.update(steps=[]),
    lambda p: p.update(created_at="2026-09-29T12:00:00"),
    lambda p: p["steps"][0]["parameters"].update(alpha=float("nan")),
])
def test_malformed_plan(resolver, context, mutation):
    payload = plan(deg())
    mutation(payload)
    assert "INVALID_WORKFLOW_PLAN" in codes(resolver.validate(payload, context))


def test_mutated_model_revalidated(resolver, context):
    model = WorkflowPlan.model_validate(plan(deg()))
    model.steps.append(model.steps[0])
    assert "INVALID_WORKFLOW_PLAN" in codes(resolver.validate(model, context))


def test_duplicate_alias(resolver, context):
    result = resolver.validate(plan(deg(output_alias="same"), volcano(output_alias="same")), context)
    assert "INVALID_WORKFLOW_PLAN" in codes(result)


def test_chinese_project_name(resolver, context):
    payload = plan(deg())
    payload["project_id"] = "黑鲷 RNA-seq"
    assert resolver.validate(payload, replace(context, project_id="黑鲷 RNA-seq")).valid


def test_project_context_checks_files(tmp_path):
    input_root = tmp_path / "input"
    input_root.mkdir()
    counts = input_root / "counts.csv"
    counts.touch()  # Existence only; content validation belongs to FileImportService.
    outside = tmp_path / "outside.csv"
    outside.touch()
    project = ProjectContext("demo", "demo", tmp_path, input_root, tmp_path / "results", tmp_path / "logs",
                             tmp_path / "reports", tmp_path / "runtime", counts,
                             input_root / "missing.csv", outside, None)
    context = WorkflowContext.from_project_context(project)
    assert context.available_inputs == frozenset({"count_matrix"})
    assert context.confirmed_parameters == {}


@pytest.mark.parametrize("change", ["missing_workflow", "wrong_input", "fake_output", "fake_dependency"])
def test_contract_drift_fails_closed(monkeypatch, change):
    registry = create_default_registry()
    original = registry.get_manifest
    def get_manifest(name):
        manifest = original(name)
        if name == "rnaseq.deg":
            meta = manifest["dependencies"]["workflow"]
            if change == "missing_workflow":
                del manifest["dependencies"]["workflow"]
            elif change == "wrong_input":
                meta["project_inputs"]["made_up"] = "metadata"
            elif change == "fake_output":
                meta["subset_source_outputs"] = ["deg_up"]
            else:
                manifest["dependencies"]["downstream"][0]["output"] = "made_up"
        return manifest
    monkeypatch.setattr(registry, "get_manifest", get_manifest)
    with pytest.raises(SkillError) as caught:
        DependencyResolver(registry)
    assert caught.value.code == "INVALID_WORKFLOW_CONTRACT"


@pytest.mark.parametrize("model", [WorkflowStep, WorkflowPlan, WorkflowResult, PlanValidationResult])
def test_schemas_exportable(model):
    schema = model.model_json_schema()
    Draft202012Validator.check_schema(schema)
    json.dumps(schema, allow_nan=False)


def test_plan_roundtrip(resolver, context):
    original = WorkflowPlan.model_validate(plan(deg(), enrichment()))
    restored = WorkflowPlan.model_validate_json(original.model_dump_json())
    assert original == restored
    result = resolver.validate(restored, context)
    assert PlanValidationResult.model_validate_json(result.model_dump_json()) == result


def test_workflow_result_reuses_skillresult():
    # Schema-only fixture, not a reported biological result or a Skill execution.
    skill = SkillResult(status="succeeded", skill_name="rnaseq.deg", run_id="actual-id",
                        outputs={"deg_csv": "fixture.csv"}).to_dict()
    result = WorkflowResult(workflow_id="workflow-id", status="succeeded", completed_steps=["deg"],
                            step_results={"deg": skill}, outputs={"full": ref()})
    assert result.step_results["deg"]["run_id"] == "actual-id"
    assert WorkflowResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("change", ["status", "unknown_output", "missing_result", "overlap", "bad_skillresult"])
def test_workflow_result_invariants(change):
    result = SkillResult(status="succeeded", skill_name="rnaseq.deg", run_id="actual-id", outputs={"deg_csv": "fixture.csv"}).to_dict()
    payload = {"workflow_id": "workflow-id", "status": "succeeded", "completed_steps": ["deg"],
               "step_results": {"deg": result}, "outputs": {"full": ref()}}
    if change == "status":
        payload["status"] = "failed"
    elif change == "unknown_output":
        payload["outputs"]["full"]["output"] = "deg_up"
    elif change == "missing_result":
        payload["step_results"] = {}
    elif change == "overlap":
        payload["skipped_steps"] = ["deg"]
    else:
        del result["schema_version"]
    with pytest.raises(ValidationError):
        WorkflowResult.model_validate(payload)


def test_malformed_skillresult_type_error_is_validation_error():
    result = SkillResult(status="succeeded", skill_name="rnaseq.deg", run_id="id").to_dict()
    result["warnings"] = [{"code": "test", "message": "fixture", "details": {"bad": object()}}]
    with pytest.raises(ValidationError):
        WorkflowResult(workflow_id="workflow", status="succeeded", completed_steps=["deg"], step_results={"deg": result})


def test_failed_and_partial_result_envelopes():
    failed = SkillResult(status="failed", skill_name="rnaseq.deg", run_id=None,
                         error_code="INVALID_INPUT", message="Fixture schema test").to_dict()
    outcome = WorkflowResult(workflow_id="wf", status="failed", failed_steps=["deg"],
                             skipped_steps=["volcano"], step_results={"deg": failed})
    assert outcome.step_results["deg"]["run_id"] is None
    successful = SkillResult(status="succeeded", skill_name="rnaseq.pca", run_id="pca-id").to_dict()
    partial = WorkflowResult(workflow_id="wf", status="partial", completed_steps=["pca"],
                             failed_steps=["deg"], step_results={"deg": failed, "pca": successful})
    assert WorkflowResult.model_validate_json(partial.model_dump_json()) == partial
