"""Serial execution of validated bindings through the existing Skill Registry only."""

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from bulk_rnaseq.deg_data import read_deg_results
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.logging_config import run_logger
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workspace import ProjectError, ProjectManager
from bulk_rnaseq.workspace.models import validate_name
from .history import ProjectHistory
from .models import PlanValidationResult, StepOutputBinding, WorkflowResult
from .validator import PlanValidator
from .comparison_inputs import prepare_comparison_inputs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowExecutor:
    """Host API, not an LLM tool. No planning, retries, shell commands or statistics."""

    def __init__(self, registry: SkillRegistry, manager: ProjectManager, project_key: str, *,
                 rscript_executable: str = "Rscript", timeout: float = 600) -> None:
        self.registry, self.manager, self.project_key = registry, manager, project_key
        self.rscript_executable, self.timeout = rscript_executable, timeout

    def execute(self, validated: PlanValidationResult, *, progress: Callable[[str, str, str], None] | None = None) -> WorkflowResult:
        try:
            return self._execute(validated, progress)
        except ProjectError as error:
            raise SkillError(error.code, str(error), details=error.details, operation="workflow") from None
        except OSError as error:
            raise SkillError("WORKFLOW_IO_ERROR", "Workflow persistence failed; inspect its checkpoint before restarting.",
                             operation="workflow", details={"error_type": type(error).__name__}) from None

    def _execute(self, validated: PlanValidationResult, progress: Callable[[str, str, str], None] | None = None) -> WorkflowResult:
        def notify(step_id: str, skill_name: str, status: str) -> None:
            if progress:
                try:
                    progress(step_id, skill_name, status)
                except Exception:
                    pass  # Rendering/disconnection must never affect DAG execution.
        if not isinstance(validated, PlanValidationResult) or not validated.valid or validated.validated_plan is None:
            raise SkillError("WORKFLOW_NOT_VALIDATED", "A successful PlanValidationResult is required.", operation="workflow")
        try:
            accepted = PlanValidationResult.model_validate(validated.model_dump())
        except (ValidationError, ValueError, TypeError):
            raise SkillError("WORKFLOW_NOT_VALIDATED", "Malformed validation envelope.", operation="workflow") from None
        project = self.manager.get_project_context(self.project_key)
        history = ProjectHistory(self.manager, self.project_key)
        checked = PlanValidator(self.registry).validate_project(accepted.validated_plan, project, history=history)
        if not checked.valid:
            raise SkillError("WORKFLOW_REVALIDATION_FAILED", "Plan or project dependencies are no longer valid.",
                             operation="workflow", details={"errors": [e.model_dump() for e in checked.errors]})
        for field in ("execution_order", "dependencies", "resolved_bindings", "resolved_parameters"):
            if getattr(accepted, field) != getattr(checked, field):
                raise SkillError("WORKFLOW_VALIDATION_CHANGED", "Validated execution contract was modified.", operation="workflow")
        plan = checked.validated_plan
        project_hashes = {b.key: sha256(self.manager._safe_path(getattr(project, b.key)))
                          for bindings in checked.resolved_bindings.values() for b in bindings.values()
                          if b.source == "project_input"}
        try:
            validate_name(plan.workflow_id)
        except ValueError:
            raise SkillError("INVALID_WORKFLOW_ID", "Workflow ID must be a portable directory name.", operation="workflow") from None
        root = self.manager._safe_path(project.output_root / "workflows" / plan.workflow_id)
        root.parent.mkdir(parents=True, exist_ok=True)
        try:
            root.mkdir()  # Atomic ownership; never overwrite a previous/aborted run.
        except FileExistsError:
            raise SkillError("WORKFLOW_ALREADY_EXISTS", "This workflow ID has already been reserved; use a new plan ID.", operation="workflow") from None
        write_json(root / "validated_plan.json", checked.model_dump(mode="json"))
        records: dict[str, Any] = {s.step_id: {
            "step_id": s.step_id, "skill_name": s.skill_name, "status": "pending", "run_id": None,
            "parameters": s.parameters, "input_bindings": {k: v.model_dump() for k, v in s.input_bindings.items()},
            "depends_on": s.depends_on, "inputs": {}, "outputs": {}, "upstream_sources": {},
        } for s in plan.steps}
        results: dict[str, dict[str, Any]] = {}
        warnings = list(plan.warnings)
        state = {"workflow_id": plan.workflow_id, "user_request": plan.user_request, "status": "running",
                 "started_at": _now(), "execution_order": checked.execution_order, "steps": records}
        write_json(root / "workflow_state.json", state)
        steps = {s.step_id: s for s in plan.steps}
        for step_id in checked.execution_order:
            notify(step_id, steps[step_id].skill_name, "pending")
        with run_logger(root / "workflow.log", plan.workflow_id) as logger:
            for index, step_id in enumerate(checked.execution_order, 1):
                step, record = steps[step_id], records[step_id]
                failed_parents = [p for p in checked.dependencies[step_id] if records[p]["status"] != "succeeded"]
                if failed_parents:
                    record.update(status="skipped", finished_at=_now(), skip_reason="UPSTREAM_FAILED", blocked_by=failed_parents)
                    logger.warning("step=%s skipped; blocked_by=%s", step_id, failed_parents)
                    write_json(root / "workflow_state.json", state)
                    notify(step_id, step.skill_name, "skipped")
                    continue
                step_root = self.manager._safe_path(root / "steps" / f"{index:03d}")
                step_root.mkdir(parents=True)
                output_dir = step_root / "skill"
                record.update(status="running", started_at=_now(), output_dir=str(output_dir))
                write_json(root / "workflow_state.json", state)
                logger.info("step=%s skill=%s execution started", step_id, step.skill_name)
                notify(step_id, step.skill_name, "running")
                result: SkillResult | None = None
                try:
                    arguments = dict(checked.resolved_parameters[step_id])
                    for parameter, binding in checked.resolved_bindings[step_id].items():
                        if binding.source == "project_input":
                            path = self.manager._safe_path(getattr(project, binding.key))
                            if not path.is_relative_to(project.input_root) or not path.is_file():
                                raise SkillError("MISSING_PROJECT_INPUT", "Bound project input is unavailable.", operation="workflow")
                            current = self.manager.get_project_context(self.project_key)
                            if getattr(current, binding.key) != path or sha256(path) != project_hashes[binding.key]:
                                raise SkillError("PROJECT_INPUT_CHANGED", "Project input changed during this workflow.", operation="workflow")
                            provenance = {**binding.model_dump(), "sha256": project_hashes[binding.key]}
                        elif binding.source == "project_result":
                            path, checksum = history.artifact(binding.run_id, binding.output)
                            provenance = {**binding.model_dump(), "sha256": checksum}
                        else:
                            source = records[binding.step_id]
                            path = self.manager._safe_path(Path(source["outputs"][binding.output]))
                            checksum = source["output_sha256"][binding.output]
                            if not path.is_file() or sha256(path) != checksum:
                                raise SkillError("UPSTREAM_ARTIFACT_CHANGED", "Upstream output is missing or has changed.", operation="workflow")
                            provenance = {**binding.model_dump(), "run_id": source["run_id"], "sha256": checksum}
                        provenance["path"] = str(path)
                        if binding.source != "project_input" and binding.gene_subset is not None:
                            path, n_genes = self._subset(path, binding.gene_subset, step_root, parameter)
                            provenance.update(selected_genes=n_genes, derived_path=str(path), derived_sha256=sha256(path))
                        record["upstream_sources"][parameter] = provenance
                        arguments[parameter] = str(path)
                    if step.skill_name == "rnaseq.deg":
                        selection = prepare_comparison_inputs(Path(arguments["count_matrix_path"]),
                            Path(arguments["metadata_path"]), arguments["treatment"], arguments["control"],
                            step_root / "bindings")
                        if selection:
                            record["sample_selection"] = {key: selection[key] for key in ("selected_samples", "excluded_samples")}
                            for parameter in ("count_matrix_path", "metadata_path"):
                                derived = selection[parameter]
                                arguments[parameter] = str(derived)
                                record["upstream_sources"][parameter].update(derived_path=str(derived), derived_sha256=sha256(derived))
                            warnings.append(f"{step_id}: PAIRWISE_SAMPLE_SELECTION: DEG used {len(selection['selected_samples'])} explicitly selected samples; other project samples were excluded from this comparison only.")
                    arguments["output_dir"] = str(output_dir)
                    schema = self.registry.get_input_schema(step.skill_name)
                    for key, value in (("rscript_executable", self.rscript_executable), ("timeout", self.timeout)):
                        if key in schema["properties"]:
                            arguments[key] = value
                    record["inputs"] = arguments
                    write_json(root / "workflow_state.json", state)
                    inputs = self.registry.validate_input(step.skill_name, arguments)
                    result = self.registry.execute(step.skill_name, inputs)
                    result = SkillResult.from_dict(result.to_dict())
                    if result.skill_name != step.skill_name:
                        raise SkillError("SKILL_RESULT_MISMATCH", "Registry result belongs to a different Skill.", operation="workflow")
                    if result.status == "succeeded":
                        for source in record["upstream_sources"].values():
                            for path_key, hash_key in (("path", "sha256"), ("derived_path", "derived_sha256")):
                                if path_key in source and sha256(self.manager._safe_path(Path(source[path_key]))) != source[hash_key]:
                                    raise SkillError("WORKFLOW_INPUT_CHANGED", "Input changed while the Skill was executing.", operation="workflow")
                        hashes = {}
                        for key, value in result.outputs.items():
                            path = self.manager._safe_path(Path(value))
                            if not path.is_relative_to(output_dir) or not path.is_file():
                                raise SkillError("INVALID_SKILL_OUTPUT", "Skill output must exist inside its reserved step directory.", operation="workflow")
                            hashes[key] = sha256(path)
                            expected = result.metadata.get("provenance", {}).get("artifact_sha256", {}).get(path.name)
                            if expected is not None and hashes[key] != expected:
                                raise SkillError("UPSTREAM_ARTIFACT_CHANGED", "Skill output differs from its recorded checksum.", operation="workflow")
                        record["output_sha256"] = hashes
                except (SkillError, ProjectError) as error:
                    result = SkillResult("failed", step.skill_name, result.run_id if result else None,
                                         error_code=error.code, message=str(error), details=error.details)
                except Exception as error:
                    # Preserve independent branches; do not expose an unhandled traceback.
                    result = SkillResult("failed", step.skill_name, result.run_id if result else None,
                                         error_code="WORKFLOW_STEP_FAILED", message="Unexpected step execution error.",
                                         details={"error_type": type(error).__name__})
                record.update(status=result.status, run_id=result.run_id, outputs=result.outputs,
                              finished_at=_now(), error_code=result.error_code, message=result.message)
                results[step_id] = result.to_dict()
                warnings.extend(f"{step_id}: {w['code']}: {w['message']}" for w in result.warnings)
                # Store transport results separately to avoid duplicate ResultManager run discovery.
                write_json(step_root / "execution_result.json", result.to_dict())
                write_json(step_root / "provenance.json", record)
                write_json(root / "workflow_state.json", state)
                logger.info("step=%s status=%s run_id=%s error_code=%s", step_id, result.status, result.run_id, result.error_code)
                notify(step_id, step.skill_name, result.status)
        completed = [s for s in checked.execution_order if records[s]["status"] == "succeeded"]
        failed = [s for s in checked.execution_order if records[s]["status"] == "failed"]
        skipped = [s for s in checked.execution_order if records[s]["status"] == "skipped"]
        final = WorkflowResult(workflow_id=plan.workflow_id, user_request=plan.user_request,
            status="succeeded" if not failed and not skipped else "partial" if completed else "failed",
            execution_order=checked.execution_order, completed_steps=completed, failed_steps=failed,
            skipped_steps=skipped, step_results=results, step_records=records, warnings=warnings,
            outputs={f"{steps[s].output_alias}.{key}": StepOutputBinding(step_id=s, output=key)
                     for s in completed if steps[s].output_alias for key in results[s]["outputs"]})
        state.update(status=final.status, finished_at=_now())
        write_json(root / "workflow_state.json", state)
        write_json(root / "workflow_result.json", final.model_dump(mode="json"))
        return final

    @staticmethod
    def _subset(source: Path, subset: str, step_root: Path, parameter: str) -> tuple[Path, int]:
        """Prepare a gene-list INPUT from significant DEG output, never recompute statistics."""
        try:
            records = read_deg_results(source)
        except SkillError as error:
            if error.code == "DEG_EMPTY":
                raise SkillError("EMPTY_GENE_SUBSET", "No significant genes are available for enrichment.", operation="workflow") from None
            raise
        if any(r.log2fc is None or r.padj is None for r in records):
            raise SkillError("INVALID_UPSTREAM_DEG", "Filtered DEG output contains missing statistics.", operation="workflow")
        genes = [r.original["gene_id"] for r in records if subset == "all"
                 or (subset == "up" and r.log2fc > 0) or (subset == "down" and r.log2fc < 0)]
        if not genes:
            raise SkillError("EMPTY_GENE_SUBSET", "No genes match the requested direction.", operation="workflow")
        folder = step_root / "bindings"
        folder.mkdir(exist_ok=True)
        target = folder / f"{parameter}.csv"
        with target.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["gene_id"])
            writer.writerows((gene,) for gene in genes)
        return target, len(genes)
