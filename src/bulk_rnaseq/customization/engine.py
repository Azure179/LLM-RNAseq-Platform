"""Host-owned, project-scoped customization. No arbitrary code or shell interface."""

from pathlib import Path
import logging
from typing import Any
from uuid import uuid4

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.run_io import analysis_run, sha256, snapshot_input, write_json
from bulk_rnaseq.workspace import ProjectManager, ProjectError
from bulk_rnaseq.workflow.history import ProjectHistory
from .catalog import parameter_schema, validate_decision
from .enrichment_plot import replot_enrichment
from .filtering import annotation_categories, filter_annotations
from .models import AnnotationFilterOptions, EnrichmentPlotOptions
from .planner import CustomizationPlan

LOGGER = logging.getLogger(__name__)

class CustomizationEngine:
    def __init__(self, manager: ProjectManager, project_key: str, registry: SkillRegistry, *,
                 rscript_executable: str = "Rscript", timeout: float = 600) -> None:
        self.manager, self.project_key, self.registry = manager, project_key, registry
        self.history = ProjectHistory(manager, project_key)
        self.rscript_executable, self.timeout = rscript_executable, timeout

    def _input_path(self, path: Path) -> Path:
        root = self.manager.open_project(self.project_key).root
        path = self.manager._safe_path(root / path)
        if not path.is_relative_to(root) or not path.is_file():
            raise SkillError("CUSTOMIZATION_PATH_REJECTED", "Only existing inputs inside the selected project are allowed.", operation="customization")
        return path

    def available_annotations(self, annotation_files: list[Path]) -> list[str]:
        return sorted({kind for path in annotation_files for kind in annotation_categories(self._input_path(path))})

    def execute(self, plan: CustomizationPlan, *, source_run_id: str,
                annotation_files: list[Path] | None = None) -> dict[str, Any]:
        """One approved plan -> new result. Original source and inputs are never overwritten."""
        customization_id = uuid4().hex
        report: dict[str, Any] = {"customization_id": customization_id, "status": "failed", "source_run_id": source_run_id,
            "source_skill": None, "mode": plan.decision.mode, "user_request": plan.user_request,
            "changed_parameters": plan.decision.parameters, "model": plan.response.model, "usage": plan.response.usage,
            "annotation_keywords": [], "annotation_sources": [], "outputs": {}, "skill_result": None,
            "validation_status": "pending"}
        root: Path | None = None
        LOGGER.info("Customization start id=%s source_run=%s mode=%s", customization_id, source_run_id, plan.decision.mode)
        try:
            project = self.manager.get_project_context(self.project_key)
            candidate = self.manager._safe_path(project.output_root / "customizations" / customization_id)
            candidate.mkdir(parents=True, exist_ok=False)
            root = candidate  # Persist only into a directory reserved by this invocation.
            annotations = [] if annotation_files is None else [self._input_path(p) for p in annotation_files]
            sources = self.available_annotations(annotations)
            decision = validate_decision(plan.decision, self.registry, plan.decision.target_skill, sources)
            if decision.mode == "unsupported":
                historical = self.history.lookup(self.project_key, source_run_id)
                report.update(status="unsupported", reason=decision.reason, validation_status="rejected",
                              source_skill=historical.skill_name if historical else None)
                write_json(root / "customization.json", report)
                LOGGER.info("Customization rejected id=%s reason=%s", customization_id, decision.reason)
                return report
            historical = self.history.lookup(self.project_key, source_run_id)
            if historical is None or historical.status != "succeeded":
                raise SkillError("MISSING_UPSTREAM_DEPENDENCY", "Select an existing successful result from this project.")
            info = self.history.results.get_run(source_run_id)
            payload = self.history.results._json(self.history.results.root / info.source)
            if info.source.endswith("tool_execution.json"):
                payload = payload["skill_result"]
            source = SkillResult.from_dict(payload) if payload.get("schema_version") == "2.0" else SkillResult.from_legacy_summary(payload)
            report["source_skill"] = source.skill_name
            target = decision.target_skill
            filtered_source = source.skill_name == "rnaseq.filter_by_annotation" and target == "rnaseq.deg" and decision.mode == "annotation_filter"
            if source.skill_name != target and not filtered_source and not (source.skill_name == "rnaseq.deg" and target == "rnaseq.volcano" and decision.mode == "parameter_override"):
                raise SkillError("CUSTOMIZATION_SOURCE_MISMATCH", "Target customization does not match the selected source Skill.")
            inputs_dir = root / "inputs"
            inputs_dir.mkdir()
            protected: dict[str, str] = {}
            bindings: dict[str, Any] = {}
            report["input_bindings"] = bindings

            def copy_input(path: Path, name: str, checksum: str | None = None) -> Path:
                path = self._input_path(path)
                checksum = checksum or sha256(path)
                destination = inputs_dir / name
                snapshot_input(path, destination)
                if sha256(path) != checksum or sha256(destination) != checksum:
                    raise SkillError("UPSTREAM_ARTIFACT_CHANGED", "Input checksum mismatch during customization snapshot.")
                protected[str(path)] = checksum
                bindings[name] = {"source_path": str(path), "sha256": checksum}
                return destination

            def artifact(key: str, name: str) -> Path:
                path, checksum = self.history.artifact(source_run_id, key)
                return copy_input(path, name, checksum)

            output = root / "skill"
            params = source.inputs.get("parameters", source.metadata.get("parameters", {}))
            if decision.mode == "annotation_filter":
                data = artifact("filtered_table" if filtered_source else "filtered_csv", "filtered_source.csv")
                annotation_copies = [copy_input(p, f"annotations_{index}{p.suffix}") for index, p in enumerate(annotations)]
                options = AnnotationFilterOptions.model_validate(decision.parameters)
                report.update(annotation_keywords=options.keywords, annotation_sources=options.annotation_sources)
                files = ("filtered_deg.csv", "matched_annotations.csv")
                with analysis_run(output, "filter_by_annotation", files) as run:
                    metrics, warnings = filter_annotations(data, annotation_copies, run.output, options)
                    result = SkillResult("succeeded", "rnaseq.filter_by_annotation", run.run_id,
                        inputs={"parameters": options.model_dump()}, outputs={"filtered_table": str(run.output / files[0]), "matched_annotations": str(run.output / files[1])},
                        summary=metrics, warnings=warnings)
            elif target in {"rnaseq.go", "rnaseq.kegg"}:
                data = artifact("enrichment", "enrichment_source.csv")
                options = EnrichmentPlotOptions.model_validate(decision.parameters)
                files = ("plot_data.csv", "dotplot.png", "barplot.png")
                with analysis_run(output, "enrichment_plot", files) as run:
                    metrics = replot_enrichment(data, run.output, target.removeprefix("rnaseq."), options,
                        rscript_executable=self.rscript_executable, timeout=self.timeout)
                    result = SkillResult("succeeded", "rnaseq.enrichment_plot", run.run_id, inputs={"parameters": options.model_dump()},
                        outputs={"plot_data": str(run.output / files[0]), "dotplot": str(run.output / files[1]), "barplot": str(run.output / files[2])},
                        summary={k: metrics[k] for k in ("n_available", "n_displayed")},
                        metadata={"processes": [metrics["process"]], "r_script_sha256": metrics["r_script_sha256"], "r_version": metrics["r_version"]},
                        warnings=([{"code": "EMPTY_ENRICHMENT", "message": "Source enrichment succeeded but contains no significant terms."}] if metrics["n_available"] == 0 else [])
                            + ([{"code": "R_DIAGNOSTICS", "message": "Inspect r_stderr.log."}] if metrics["process"]["stderr"].strip() else []))
            else:
                allowed = parameter_schema(self.registry, target)["properties"]
                arguments = {k: v for k, v in params.items() if k in allowed}
                if target == "rnaseq.volcano":
                    key = "deg_csv" if source.skill_name == "rnaseq.deg" else "plot_data"
                    arguments["deg_result_file"] = str(artifact(key, "deg_source.csv"))
                    if source.skill_name == "rnaseq.deg":
                        arguments.update(padj_threshold=params["alpha"], log2fc_threshold=params["lfc_threshold"])
                else:
                    arguments["normalized_expression"] = str(artifact("normalized_expression", "vst_source.csv"))
                    for key in ("metadata", "deg_result_file"):
                        checksum = source.metadata.get("provenance", {}).get("input_sha256", {}).get(key)
                        if not checksum:
                            raise SkillError("UPSTREAM_PROVENANCE_MISSING", "Existing Heatmap inputs require their recorded checksums.")
                        arguments[key] = str(copy_input(Path(source.inputs[key]), key + ".csv", checksum))
                    arguments["group_column"] = params["group_column"]
                arguments.update(decision.parameters)
                arguments.update(output_dir=str(output), rscript_executable=self.rscript_executable, timeout=self.timeout)
                result = self.registry.execute(target, self.registry.validate_input(target, arguments))
            report["skill_result"] = result.to_dict()
            for path, checksum in protected.items():
                if sha256(self._input_path(Path(path))) != checksum:
                    raise SkillError("UPSTREAM_ARTIFACT_CHANGED", "Source changed while customization was running.")
            if result.status == "succeeded":
                artifacts = {}
                for key, value in result.outputs.items():
                    path = self._input_path(Path(value))
                    if not path.is_relative_to(output) or path.stat().st_size == 0:
                        raise SkillError("CUSTOM_OUTPUT_VALIDATION_FAILED", "Artifact must be non-empty and inside customization output.")
                    artifacts[path.name] = sha256(path)
                # Keep original Skill summary untouched. This transport result adds customization provenance.
                data = result.to_dict()
                data["metadata"]["customization"] = {"customization_id": customization_id, "source_run_id": source_run_id, "source_skill": source.skill_name}
                data["metadata"].setdefault("provenance", {})["artifact_sha256"] = artifacts
                write_json(output / "skill_result.json", data)
                report.update(status="succeeded", outputs=result.outputs, skill_result=data, validation_status="passed")
            else:
                report.update(error_code=result.error_code, message=result.message, validation_status="execution_failed")
            report["input_bindings"] = bindings
        except (SkillError, ProjectError) as error:
            report.update(error_code=error.code, message=str(error), validation_status="failed")
        except (OSError, ValueError, TypeError, KeyError) as error:
            report.update(error_code="CUSTOMIZATION_FAILED", message="Customization failed; original results remain unchanged.",
                          details={"error_type": type(error).__name__}, validation_status="failed")
        if root is not None:
            try:
                write_json(root / "customization.json", report)
            except OSError:
                report.update(status="failed", error_code="CUSTOMIZATION_REPORT_IO_ERROR", message="Unable to persist customization provenance.")
        LOGGER.info("Customization finished id=%s status=%s validation=%s", customization_id, report["status"], report["validation_status"])
        return report
